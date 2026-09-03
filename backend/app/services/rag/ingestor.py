"""
知识库入库管线（后台任务入口）。

流程：保存文件 → 解析 docx → 结构感知分块 → [可选]LLM 摘要增强
     → 双通道索引（BM25 恒建 + 嵌入如已配置）→ [可选]LLM 条目策展

全程通过 TaskContext 上报进度；失败时文档状态置 error 并保留原因。
"""
import logging
import uuid
from pathlib import Path

from app.core.config import settings
from app.core.task_manager import TaskContext
from app.db.database import get_session
from app.db.models import KBDocument
from app.services.parser.word_parser import WordDocumentParser
from app.services.rag.chunker import chunk_parsed_document
from app.services.rag.enricher import enrich_chunks_inplace
from app.services.rag.indexer import knowledge_index, now_str
from app.services.rag import curator

logger = logging.getLogger("easywrite.rag.ingest")

_parser = WordDocumentParser()


def ingest_document(file_bytes: bytes, doc_name: str, curate: bool = True) -> str:
    """同步入库（供测试与后台任务共用），返回 task 风格错误时抛异常。"""

    class _NullCtx:
        def report(self, *a, **k):
            pass

    return _ingest_impl(file_bytes, doc_name, curate, _NullCtx())


def ingest_document_task(file_bytes: bytes, doc_name: str, curate: bool, ctx: TaskContext):
    """后台任务包装"""
    doc_id = _ingest_impl(file_bytes, doc_name, curate, ctx)
    return {"doc_id": doc_id, "doc_name": doc_name}


def _ingest_impl(file_bytes: bytes, doc_name: str, curate: bool, ctx) -> str:
    doc_id = f"kbdoc_{uuid.uuid4().hex[:12]}"

    # 预创建文档记录（ingesting 状态，前端可见进行中）
    with get_session() as session:
        session.add(KBDocument(
            id=doc_id, doc_name=doc_name, status="ingesting",
            created_at=now_str(), updated_at=now_str(),
        ))

    try:
        # 1. 保存原件
        ctx.report(5, "保存上传文件")
        safe_name = Path(doc_name).name
        saved_path = settings.UPLOAD_DIR / f"{doc_id}_{safe_name}"
        saved_path.write_bytes(file_bytes)

        # 2. 解析
        ctx.report(15, "解析 Word 文档结构")
        parsed = _parser.parse_docx(saved_path)

        # 3. 语义分块
        ctx.report(35, "结构感知语义分块")
        chunks = chunk_parsed_document(parsed, doc_name=doc_name, doc_id=doc_id)
        if not chunks:
            raise ValueError("文档解析后未产生有效内容块（可能是空文档或纯图片扫描件）")

        # 4. LLM 摘要增强（可选，配置开关）
        enrich_chunks_inplace(doc_name, chunks, progress=ctx)

        # 5. 双通道索引
        ctx.report(70, "构建 BM25 / 向量双通道索引")
        added = knowledge_index.add_document(doc_id, doc_name, str(saved_path), chunks)

        # 6. 更新文档记录
        with get_session() as session:
            doc = session.get(KBDocument, doc_id)
            if doc:
                doc.file_path = str(saved_path)
                doc.chunk_count = added
                doc.status = "ready"
                doc.updated_at = now_str()
                session.add(doc)

        # 7. LLM 条目策展（可选）
        if curate:
            ctx.report(80, "LLM 知识条目策展")
            try:
                curator.curate_document(doc_id, progress=ctx)
            except Exception as e:
                logger.warning("条目策展失败（不影响入库结果）: %s", e)

        ctx.report(100, f"入库完成：{added} 个知识块")
        return doc_id

    except Exception as e:
        logger.exception("文档入库失败: %s", doc_name)
        with get_session() as session:
            doc = session.get(KBDocument, doc_id)
            if doc:
                doc.status = "error"
                doc.error_msg = str(e)[:500]
                doc.updated_at = now_str()
                session.add(doc)
        raise
