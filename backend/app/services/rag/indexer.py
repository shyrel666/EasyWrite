"""
混合检索索引（BM25 + 稠密向量 + RRF 融合）。

架构：
- 持久层：SQLite kb_chunks（正文/上下文文本/向量 BLOB/版本指纹）
- 内存层：BM25Okapi 语料（jieba 分词）+ 稠密向量矩阵（numpy 余弦）
- 融合：Reciprocal Rank Fusion——只依赖排名而非分数，天然免疫两路分数量纲差异
  （替代原 0.6/0.4 线性加权：TF-IDF 余弦 ~0.0-0.3 vs 嵌入余弦 ~0.6-0.9，从未校准过）

线程安全：所有内存索引变更持锁重建；SQLite 由 SQLModel 引擎管理。
"""
import json
import logging
import re
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import jieba
from rank_bm25 import BM25Okapi
from sqlmodel import select, delete as sql_delete

from app.core.config import settings
from app.core.embedding_client import embedding_client
from app.db.database import get_session
from app.db.models import KBChunk, KBDocument

logger = logging.getLogger("easywrite.rag.index")

_TOKEN_JUNK = re.compile(r"^[\W_]+$")


def tokenize_zh(text: str) -> List[str]:
    """jieba 分词，剔除纯标点与空白 token"""
    return [t for t in jieba.lcut(text) if t.strip() and not _TOKEN_JUNK.match(t)]


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def embedding_version() -> str:
    if embedding_client.is_available:
        return f"{embedding_client.provider}:{embedding_client.model}"
    return ""


class KnowledgeIndex:
    def __init__(self):
        self._lock = threading.RLock()
        self._chunks: Dict[str, dict] = {}          # chunk_id -> 运行时块
        self._corpus_ids: List[str] = []            # BM25 语料对齐的 id 顺序
        self._bm25: Optional[BM25Okapi] = None
        self._dense_matrix: Optional[np.ndarray] = None
        self._dense_ids: List[str] = []
        self.reload()

    # ---------------- 加载与重建 ----------------

    def reload(self):
        """从 SQLite 全量加载并重建内存索引（启动/文档增删后调用）"""
        with self._lock:
            self._chunks = {}
            with get_session() as session:
                for row in session.exec(select(KBChunk)).all():
                    self._chunks[row.id] = self._row_to_runtime(row)
            self._rebuild_bm25()
            self._rebuild_dense()

    @staticmethod
    def _row_to_runtime(row: KBChunk) -> dict:
        return {
            "id": row.id,
            "doc_id": row.doc_id,
            "seq": row.seq,
            "section_title": row.section_title,
            "breadcrumb": row.breadcrumb,
            "content": row.content,
            "context_text": row.context_text,
            "tags": json.loads(row.tags_json or "[]"),
            "parent_summary": row.parent_summary or "",
            "embedding_version": row.embedding_version or "",
            "_embedding": np.frombuffer(row.embedding, dtype=np.float32) if row.embedding else None,
        }

    def _rebuild_bm25(self):
        ids = list(self._chunks.keys())
        if not ids:
            self._corpus_ids, self._bm25 = [], None
            return
        corpus = [tokenize_zh(self._chunks[cid]["context_text"]) for cid in ids]
        # 空文档 token 列表会导致 BM25 除零，以占位符填充
        corpus = [c if c else ["空"] for c in corpus]
        self._corpus_ids = ids
        self._bm25 = BM25Okapi(corpus)

    def _rebuild_dense(self):
        ids, vecs = [], []
        for cid, c in self._chunks.items():
            if c["_embedding"] is not None and len(c["_embedding"]) > 0:
                ids.append(cid)
                vecs.append(c["_embedding"])
        self._dense_ids = ids
        self._dense_matrix = np.vstack(vecs) if vecs else None

    # ---------------- 文档管理 ----------------

    def add_document(self, doc_id: str, doc_name: str, file_path: str, chunks: List[dict]) -> int:
        """写入块 + 计算向量 + 重建内存索引。返回入库块数。"""
        if not chunks:
            return 0

        emb_version = embedding_version()
        texts = [c["context_text"] for c in chunks]
        vectors = embedding_client.embed_texts_sync(texts) if emb_version else None

        with get_session() as session:
            # merge：兼容入库器预创建的 ingesting 记录（存在则更新为 ready）
            session.merge(KBDocument(
                id=doc_id, doc_name=doc_name, file_path=file_path,
                status="ready", chunk_count=len(chunks),
                embedding_version=emb_version, created_at=now_str(), updated_at=now_str(),
            ))
            for i, c in enumerate(chunks):
                vec = vectors[i] if vectors is not None else None
                session.add(KBChunk(
                    id=c["id"], doc_id=doc_id, seq=c.get("seq", i),
                    section_title=c.get("section_title", ""),
                    breadcrumb=c.get("breadcrumb", ""),
                    content=c.get("content", ""),
                    context_text=c.get("context_text", ""),
                    tags_json=json.dumps(c.get("tags", []), ensure_ascii=False),
                    parent_summary=c.get("parent_summary", ""),
                    embedding=vec.tobytes() if vec is not None else None,
                    embedding_version=emb_version if vec is not None else "",
                ))

        self.reload()
        return len(chunks)

    def delete_document(self, doc_id: str) -> bool:
        with get_session() as session:
            doc = session.get(KBDocument, doc_id)
            if not doc:
                return False
            session.exec(sql_delete(KBChunk).where(KBChunk.doc_id == doc_id))  # type: ignore[arg-type]
            session.delete(doc)
        self.reload()
        return True

    def reindex_embeddings(self, progress=None) -> int:
        """嵌入模型变更后重建全部向量（BM25 无需重建）"""
        emb_version = embedding_version()
        if not emb_version:
            return 0
        stale_ids = [cid for cid, c in self._chunks.items() if c["embedding_version"] != emb_version]
        if not stale_ids:
            return 0
        total = len(stale_ids)
        done = 0
        batch = 32
        for i in range(0, total, batch):
            ids = stale_ids[i : i + batch]
            texts = [self._chunks[cid]["context_text"] for cid in ids]
            vectors = embedding_client.embed_texts_sync(texts)
            if vectors is None:
                raise RuntimeError("嵌入接口调用失败，向量重建中止（已完成的批次保留）")
            with get_session() as session:
                for j, cid in enumerate(ids):
                    row = session.get(KBChunk, cid)
                    if row:
                        row.embedding = vectors[j].tobytes()
                        row.embedding_version = emb_version
                        session.add(row)
                doc = session.get(KBDocument, self._chunks[ids[0]]["doc_id"])
                if doc:
                    doc.embedding_version = emb_version
                    doc.updated_at = now_str()
                    session.add(doc)
            done += len(ids)
            if progress:
                progress.report(int(done / total * 100), f"向量重建 {done}/{total}")
        self.reload()
        return done

    # ---------------- 检索 ----------------

    def search_bm25(self, query: str, top_k: int = 20, doc_filter: Optional[List[str]] = None) -> List[Tuple[str, float]]:
        with self._lock:
            if not self._bm25:
                return []
            tokens = tokenize_zh(query)
            if not tokens:
                return []
            scores = self._bm25.get_scores(tokens)
            order = np.argsort(-scores)
            results = []
            for idx in order[: top_k * 3]:
                cid = self._corpus_ids[int(idx)]
                if doc_filter and self._chunks[cid]["doc_id"] not in doc_filter:
                    continue
                results.append((cid, float(scores[idx])))
                if len(results) >= top_k:
                    break
            return results

    def search_dense(self, query: str, top_k: int = 20, doc_filter: Optional[List[str]] = None) -> List[Tuple[str, float]]:
        with self._lock:
            if self._dense_matrix is None or not embedding_client.is_available:
                return []
            q = embedding_client.embed_query_sync(query)
            if q is None:
                return []
            q_norm = q / (np.linalg.norm(q) + 1e-10)
            mat = self._dense_matrix / (np.linalg.norm(self._dense_matrix, axis=1, keepdims=True) + 1e-10)
            scores = mat @ q_norm
            order = np.argsort(-scores)
            results = []
            for idx in order[: top_k * 3]:
                cid = self._dense_ids[int(idx)]
                if doc_filter and self._chunks[cid]["doc_id"] not in doc_filter:
                    continue
                results.append((cid, float(scores[idx])))
                if len(results) >= top_k:
                    break
            return results

    @staticmethod
    def fuse_rrf(ranked_lists: List[List[Tuple[str, float]]], k: int = 60) -> List[Tuple[str, float]]:
        """Reciprocal Rank Fusion：score = Σ 1/(k + rank)"""
        k = k or settings.RAG_RRF_K
        fused: Dict[str, float] = {}
        for lst in ranked_lists:
            for rank, (cid, _) in enumerate(lst):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (k + rank + 1)
        return sorted(fused.items(), key=lambda x: -x[1])

    # ---------------- 读取 ----------------

    def get_chunk(self, chunk_id: str) -> Optional[dict]:
        return self._chunks.get(chunk_id)

    def get_chunks(self, chunk_ids: List[str]) -> List[dict]:
        return [self._chunks[cid] for cid in chunk_ids if cid in self._chunks]

    def list_documents(self) -> List[dict]:
        with get_session() as session:
            docs = session.exec(select(KBDocument).order_by(KBDocument.created_at.desc())).all()  # type: ignore[union-attr]
            return [
                {
                    "id": d.id, "doc_name": d.doc_name, "status": d.status,
                    "chunk_count": d.chunk_count, "item_count": d.item_count,
                    "embedding_version": d.embedding_version,
                    "error_msg": d.error_msg, "created_at": d.created_at,
                }
                for d in docs
            ]

    def get_doc_name(self, doc_id: str) -> str:
        with get_session() as session:
            doc = session.get(KBDocument, doc_id)
            return doc.doc_name if doc else ""

    def stats(self) -> dict:
        docs = self.list_documents()
        with_embeddings = sum(
            1 for c in self._chunks.values() if c["_embedding"] is not None
        )
        current_version = embedding_version()
        stale = sum(
            1 for c in self._chunks.values()
            if c["_embedding"] is not None and c["embedding_version"] != current_version
        )
        return {
            "total_chunks": len(self._chunks),
            "total_documents": len(docs),
            "documents": docs,
            "chunks_with_embeddings": with_embeddings,
            "dense_enabled": embedding_client.is_available,
            "embedding_model": embedding_client.model if embedding_client.is_available else "",
            "embedding_stale_count": stale,
        }

    def all_tags(self) -> List[str]:
        tags = set()
        for c in self._chunks.values():
            tags.update(c["tags"])
        return sorted(tags)


knowledge_index = KnowledgeIndex()
