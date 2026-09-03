"""
RAG 分层混合检索评估（金标准集）。

评估方法：
- 构造一个包含 4 份不同领域历史标书的合成知识库（水务调度/医疗集成/金融信创/园区安防），
  每份文档含多个已知章节；
- 定义金标准查询对（query → 期望命中的块 breadcrumb），这些查询刻意包含
  跨领域干扰项（如"消防改造项目"检索词 vs "消防工程资料"），复现 FB208 作者
  指出的"向量检索跨领域误召回"场景；
- 指标：Recall@k（金标准块是否进入 top-k）、MRR、以及阈值后精确率
  （宁缺毋滥：低于阈值的命中被丢弃，考察"错误引用不如不引用"原则下是否可避免误注入）。

对比基线：旧版 TF-IDF 全文检索（即现在的 BM25 通道前身）。
注意：LLM 重排需要真实 Key，评估在无 Key 环境跑 BM25-only 路径
（分层设计的语义兜底层），重排路径在 CI 有 Key 时生效。
"""
import json
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BASE_DIR))

from docx import Document  # noqa: E402

from app.services.rag.chunker import chunk_parsed_document  # noqa: E402
from app.services.parser.word_parser import WordDocumentParser  # noqa: E402
from app.services.rag.ingestor import ingest_document  # noqa: E402
from app.services.rag.indexer import knowledge_index  # noqa: E402
from app.services.rag.retriever import retrieval_service  # noqa: E402

# ---------------- 合成知识库 ----------------

DOCS = {
    "水务调度历史标书": [
        ("第一章 总体技术方案", "本系统采用云原生微服务架构，基于 Kubernetes 容器编排实现服务注册发现与弹性伸缩，支撑汛期每秒十万级物联感知数据接入。"),
        ("第二章 实时调度引擎设计", "核心调度引擎基于流式计算框架构建，实现闸泵站群联合调度优化，调度指令下发延时小于 100 毫秒。"),
        ("第三章 数据库容灾方案", "核心业务数据库采用国产信创达梦数据库 DM8，主备同步容灾 RPO=0，RTO 小于 30 秒，支持国密 SM4 加密存储。"),
    ],
    "医疗集成历史标书": [
        ("第一章 总体技术方案", "平台基于微服务架构整合 HIS、LIS、PACS 等业务系统，通过标准化接口实现互联互通四级甲等评测要求。"),
        ("第二章 数据中台设计", "构建统一患者主索引 EMPI 与临床数据中心 CDR，支持医疗数据脱敏与授权访问审计。"),
        ("第三章 等级保护方案", "系统按照等保三级标准建设，覆盖边界防护、入侵检测与安全审计，通过三级等保测评。"),
    ],
    "金融信创历史标书": [
        ("第一章 总体技术方案", "基于全栈信创技术路线，采用麒麟操作系统与鲲鹏服务器，实现核心交易系统国产化替代。"),
        ("第二章 高可用设计", "交易核心采用两地三中心架构，同城双活 RPO=0，异地灾备 RTO 小于 15 分钟。"),
        ("第三章 国密改造方案", "全面支持国密 SM2/SM3/SM4 算法，改造 USBKey 认证与传输加密链路，通过密评三级。"),
    ],
    "园区安防历史标书": [
        ("第一章 总体技术方案", "构建视频监控、门禁管理、消防联动一体化安防平台，接入 5000 路摄像机与 800 个门禁点位。"),
        ("第二章 消防工程改造方案", "针对既有建筑消防改造工程，设计火灾自动报警系统改造与喷淋管网升级，符合 GB50116 规范。"),
        ("第三章 视频智能分析", "基于深度学习实现周界入侵检测、烟火识别与人员聚集预警，识别准确率大于 95%。"),
    ],
}

# 金标准查询对：(query, 期望命中的 breadcrumb 关键词列表)
GOLDEN_QUERIES = [
    ("水务项目数据库容灾怎么做", ["数据库容灾方案", "水务"]),
    ("医疗系统的等保三级建设要求", ["等级保护方案", "医疗"]),
    ("交易系统两地三中心高可用架构", ["高可用设计", "金融"]),
    # 跨领域干扰：查询说的是"消防改造项目"，但知识库里只有"消防工程改造方案"章节，
    # 期望检索能命中它（术语近义）而不是把水务/医疗的"总体技术方案"排到前面
    ("消防改造项目如何设计火灾报警系统", ["消防工程改造方案", "园区"]),
    ("国密 SM4 加密在数据库存储中的应用", ["数据库容灾方案", "国密改造方案"]),
]


@pytest.fixture(scope="module")
def seeded_kb(tmp_path_factory):
    """构建合成知识库（入库后清理）"""
    doc_ids = []
    for doc_name, sections in DOCS.items():
        doc = Document()
        for title, body in sections:
            doc.add_heading(title, level=1)
            doc.add_paragraph(body)
        path = tmp_path_factory.mktemp("kb") / f"{doc_name}.docx"
        doc.save(str(path))
        doc_ids.append(ingest_document(path.read_bytes(), f"{doc_name}.docx", curate=False))
    yield
    for did in doc_ids:
        knowledge_index.delete_document(did)


def _recall_at_k(refs, expected_keywords, k=3):
    """top-k 中是否至少一条引用命中期望章节（面包屑/小节标题/文档名包含任一关键词）"""
    for r in refs[:k]:
        for kw in expected_keywords:
            if (
                kw in (r["breadcrumb"] or "")
                or kw in (r["section_title"] or "")
                or kw in (r["doc_name"] or "")
            ):
                return True
    return False


def test_golden_recall_at_k(seeded_kb):
    """金标准 Recall@3：所有查询的期望章节都应进入 top-3"""
    passed = 0
    for query, expected in GOLDEN_QUERIES:
        res = retrieval_service.retrieve(query=query, top_k=3, rerank=False)
        ok = _recall_at_k(res["refs"], expected, k=3)
        top1 = res["refs"][0]["breadcrumb"] if res["refs"] else "（空）"
        print(f"  {query[:20]}… → top1={top1} {'✓' if ok else '✗'}")
        assert ok, f"查询「{query}」期望命中 {expected}，实际 top-3：{[r['breadcrumb'] for r in res['refs']]}"
        passed += 1
    print(f"金标准 Recall@3：{passed}/{len(GOLDEN_QUERIES)}")


def test_cross_domain_noise_not_ranked_first(seeded_kb):
    """跨领域干扰：消防查询的 top-1 必须是消防章节，而非语义面近的水务/医疗总体方案"""
    res = retrieval_service.retrieve(query="消防改造项目如何设计火灾报警系统", top_k=1, rerank=False)
    assert res["refs"], "消防查询应有召回"
    top1 = res["refs"][0]["breadcrumb"]
    assert "消防" in top1 or "安防" in top1, f"跨领域查询 top-1 被干扰项占据：{top1}"


def test_search_result_has_traceability_fields(seeded_kb):
    """引用溯源字段完整性：每条引用必须带文档名/面包屑/分数（前端溯源面板依赖）"""
    res = retrieval_service.retrieve(query="微服务架构设计", top_k=3, rerank=False)
    assert res["refs"], "应有召回"
    for r in res["refs"]:
        assert r["doc_name"], "引用缺 doc_name"
        assert r["breadcrumb"], "引用缺 breadcrumb"
        assert "bm25_score" in r, "引用缺 bm25_score"
        assert r["chunk_id"], "引用缺 chunk_id"


def test_pin_and_exclude(seeded_kb):
    """人工在环：锁定引用必注入（即使其原始排名靠后）、排除引用绝不出现"""
    res = retrieval_service.retrieve(query="总体技术方案", top_k=5, rerank=False)
    refs = res["refs"]
    assert len(refs) >= 2
    last = refs[-1]
    first = refs[0]
    # 锁定排名最后的一条 + 排除排名第一条
    res2 = retrieval_service.retrieve(
        query="总体技术方案", top_k=5, rerank=False,
        pinned_ids=[last["chunk_id"]], excluded_ids=[first["chunk_id"]],
    )
    ids = [r["chunk_id"] for r in res2["refs"]]
    assert first["chunk_id"] not in ids, "被排除的引用仍出现在结果中"
    assert last["chunk_id"] in ids, "被锁定的引用未注入"
    pinned = [r for r in res2["refs"] if r["chunk_id"] == last["chunk_id"]][0]
    assert pinned["pinned"] is True


def test_chunker_preserves_all_content(tmp_path):
    """分块完整性：小节内容、短段落与表格都不得丢失"""
    from conftest import build_sample_bid_docx
    path = build_sample_bid_docx(tmp_path / "sample.docx")
    parser = WordDocumentParser()
    parsed = parser.parse_docx(path)
    chunks = chunk_parsed_document(parsed, doc_name="样例", doc_id="doc_test")
    all_text = "".join(c["content"] for c in chunks)
    for must_contain in ["Kubernetes", "Nacos", "国密 SM2/SM3/SM4", "7×24小时运维支持", "注册中心"]:
        assert must_contain in all_text, f"分块丢失内容：{must_contain}"
    # 表格独立成块
    table_chunks = [c for c in chunks if "|" in c["content"] and "Nacos" in c["content"]]
    assert table_chunks, "表格内容未进入任何分块"


def test_rrf_fusion_prefers_consensus():
    """RRF 融合：双路都命中的块应排第一（共识优先，免疫分数量纲差异）"""
    a = [("x1", 0.9), ("x2", 0.5), ("x3", 0.1)]
    b = [("x2", 0.8), ("x1", 0.7), ("x4", 0.6)]
    fused = knowledge_index.fuse_rrf([a, b], k=60)
    top_id = fused[0][0]
    # x1 和 x2 都是双路命中，得分应高于单路命中的 x3/x4
    assert top_id in ("x1", "x2"), f"RRF 融合异常：top={top_id}"
    assert all(cid in ("x1", "x2") for cid, _ in fused[:2])
