"""
核心流水线算法闭环测试：
样例生成 → Word 解析 → 语义分块 → 混合索引 → 检索 → 章节草拟（引用注入）→ Word 导出。
"""
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BASE_DIR))

from app.services.parser.word_parser import WordDocumentParser  # noqa: E402
from app.services.rag.chunker import chunk_parsed_document  # noqa: E402
from app.services.rag.ingestor import ingest_document  # noqa: E402
from app.services.rag.indexer import knowledge_index  # noqa: E402
from app.services.rag.retriever import retrieval_service  # noqa: E402
from app.services.generator.section_generator import section_generator  # noqa: E402
from app.services.exporter.docx_generator import docx_exporter  # noqa: E402
from app.models.schemas import GlobalFacts, OutlineNode  # noqa: E402
from conftest import build_sample_bid_docx  # noqa: E402


@pytest.fixture()
def kb_doc(tmp_path):
    path = build_sample_bid_docx(tmp_path / "历史标书样例.docx")
    doc_id = ingest_document(path.read_bytes(), "历史标书样例.docx", curate=False)
    yield doc_id
    knowledge_index.delete_document(doc_id)


def test_parse_and_chunk(tmp_path):
    """解析：层级大纲 + 面包屑正确；分块：携带溯源元数据"""
    path = build_sample_bid_docx(tmp_path / "样例.docx")
    parsed = WordDocumentParser().parse_docx(path)
    assert parsed["total_sections"] >= 4
    chunks = chunk_parsed_document(parsed, doc_name="样例", doc_id="d1")
    assert len(chunks) >= 3
    for c in chunks:
        assert c["breadcrumb"], "分块缺面包屑"
        assert c["context_text"], "分块缺上下文增强文本"


def test_retrieve_and_draft_with_refs(kb_doc, tmp_path):
    """检索命中 → 章节草拟提示词携带引用（溯源）"""
    res = retrieval_service.retrieve(query="数据库容灾与数据加密", top_k=2, rerank=False)
    assert res["refs"], "检索无召回"
    assert "安全与等保" in res["refs"][0]["breadcrumb"] or "容灾" in res["refs"][0]["breadcrumb"] \
        or "SM2" in res["refs"][0]["content"]

    facts = GlobalFacts(
        company_name="北京华泰智联数智科技有限公司",
        core_product_name="HT-AquaCloud 智慧水务调度中枢v6.0",
        architecture_stack="云原生微服务 + Kubernetes 多活集群",
        database_selection="国产信创达梦数据库 DM8",
    )
    outline = [OutlineNode(id="sec_2", title="第二章 数据安全与容灾设计", level=1, path="第二章 数据安全与容灾设计")]
    built = section_generator.build_prompts(
        section_title="2.1 数据容灾与加密方案",
        section_path="第二章 数据安全与容灾设计 > 2.1 数据容灾与加密方案",
        requirements=["核心数据加密存储", "RPO=0"],
        facts=facts, outline=outline, section_id="sec_2",
    )
    # 引用注入提示词（含溯源块）
    assert "参考资料" in built["user"]
    assert built["refs"], "草拟应携带引用溯源"

    result = section_generator.draft_section(
        section_title="2.1 数据容灾与加密方案",
        section_path="第二章 数据安全与容灾设计 > 2.1 数据容灾与加密方案",
        requirements=["核心数据加密存储"],
        facts=facts, outline=outline, section_id="sec_2",
    )
    assert result["generated_content"]
    assert result["mode"] in ("llm", "mock")


def test_export_with_toc(tmp_path):
    """导出：封面事实填充 + 目录域 + 页码域 + Mermaid 渲染"""
    facts = GlobalFacts(
        company_name="北京华泰智联数智科技有限公司",
        legal_rep="李四", credit_code="91110108MA99887766",
        core_product_name="HT-AquaCloud 智慧水务调度中枢v6.0",
        database_selection="国产信创达梦数据库 DM8",
    )
    outline = [
        OutlineNode(
            id="sec_1", title="第一章 总体技术方案", level=1, path="第一章 总体技术方案",
            status="completed",
            content="### 1. 总体架构\n本方案基于云原生微服务架构。\n\n```mermaid\ngraph TD\n    A[接入层] --> B[网关层]\n    B --> C[微服务业务层]\n```\n\n| 组件 | 选型 |\n| --- | --- |\n| 注册中心 | Nacos |",
        ),
    ]
    out = docx_exporter.export_project_to_docx(
        project_name="测试导出项目", client_name="某采购人",
        outline=outline, facts=facts, output_filename="pipeline_test.docx",
    )
    assert out.exists() and out.stat().st_size > 5000
    # 目录域在正文部件，页码域在页脚部件
    import zipfile
    with zipfile.ZipFile(out) as z:
        document_xml = z.read("word/document.xml").decode("utf-8")
        footer_parts = [n for n in z.namelist() if "footer" in n]
        footer_xml = "\n".join(z.read(n).decode("utf-8") for n in footer_parts)
    assert "TOC" in document_xml, "导出文档缺少目录域"
    assert "PAGE" in footer_xml and "NUMPAGES" in footer_xml, "导出文档页脚缺少页码域"
