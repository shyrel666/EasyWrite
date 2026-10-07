"""知识库路由：入库（后台任务）/ 文档管理 / 混合检索 / 条目策展"""
import logging

from fastapi import APIRouter, HTTPException, UploadFile, File

from app.core.task_manager import task_manager
from app.models.schemas import KnowledgeQueryRequest
from app.services.rag.indexer import knowledge_index
from app.services.rag.retriever import retrieval_service
from app.services.rag import curator
from app.services.rag.ingestor import ingest_document_task
from app.services.parser.document_parser import unsupported_reason

logger = logging.getLogger("easywrite.api.knowledge")
router = APIRouter(prefix="/knowledge", tags=["知识库"])


@router.post("/upload", summary="上传历史标书/方案（.docx / .pdf）并后台入库（分块+索引+可选策展）")
async def upload_historical_bid(file: UploadFile = File(...), curate: bool = True):
    reason = unsupported_reason(file.filename)
    if reason:
        raise HTTPException(status_code=400, detail=reason)

    content = await file.read()
    task_id = task_manager.submit(
        "kb_ingest",
        lambda ctx: ingest_document_task(content, file.filename, curate, ctx),
        description=f"知识库入库：{file.filename}",
    )
    return {"task_id": task_id, "filename": file.filename}


@router.get("/documents", summary="知识库文档列表（含入库状态与进度入口）")
def list_documents():
    return {"documents": knowledge_index.list_documents()}


@router.delete("/documents/{doc_id}", summary="删除文档及其全部索引块")
def delete_document(doc_id: str):
    if not knowledge_index.delete_document(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"status": "success", "deleted_id": doc_id}


@router.post("/documents/{doc_id}/reindex", summary="嵌入模型变更后重建该文档向量（后台任务）")
def reindex_document(doc_id: str):
    docs = [d for d in knowledge_index.list_documents() if d["id"] == doc_id]
    if not docs:
        raise HTTPException(status_code=404, detail="文档不存在")

    def _run(ctx):
        from app.services.rag.indexer import embedding_version
        # 重建以当前嵌入模型为目标的全部陈旧向量（简单起见全量重建）
        done = knowledge_index.reindex_embeddings(progress=ctx)
        return {"reembedded": done, "embedding_version": embedding_version()}

    task_id = task_manager.submit("kb_reindex", _run, description=f"向量重建：{docs[0]['doc_name']}")
    return {"task_id": task_id}


@router.post("/documents/{doc_id}/curate", summary="对文档执行 LLM 知识条目策展（后台任务）")
def curate_document_endpoint(doc_id: str):
    docs = [d for d in knowledge_index.list_documents() if d["id"] == doc_id]
    if not docs:
        raise HTTPException(status_code=404, detail="文档不存在")

    task_id = task_manager.submit(
        "kb_curate",
        lambda ctx: {"item_count": curator.curate_document(doc_id, progress=ctx)},
        description=f"条目策展：{docs[0]['doc_name']}",
    )
    return {"task_id": task_id}


@router.get("/stats", summary="知识库统计（块数/文档数/嵌入覆盖）")
def get_knowledge_stats():
    stats = knowledge_index.stats()
    stats["available_tags"] = knowledge_index.all_tags()
    return stats


@router.post("/search", summary="分层混合检索（BM25+向量→RRF→LLM重排→阈值）")
def search_knowledge(req: KnowledgeQueryRequest):
    return retrieval_service.retrieve(
        query=req.query,
        top_k=req.top_k,
        rerank=req.rerank,
        doc_filter=req.doc_filter,
    )


@router.get("/items", summary="知识条目列表（LLM 策展产物，供大纲路由）")
def list_items(doc_id: str = None):
    return {"items": curator.list_items(doc_id)}
