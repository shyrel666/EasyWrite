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
from app.db.models import KBChunk, KBDocument, KBItem

logger = logging.getLogger("easywrite.rag.index")

REINDEX_BATCH = 32  # 向量重建每批块数（每批一次嵌入请求）

_TOKEN_JUNK = re.compile(r"^[\W_]+$")


def tokenize_zh(text: str) -> List[str]:
    """jieba 分词，剔除纯标点与空白 token"""
    return [t for t in jieba.lcut(text) if t.strip() and not _TOKEN_JUNK.match(t)]


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def embedding_version() -> str:
    return embedding_client.snapshot().version


class KnowledgeIndex:
    def __init__(self):
        self._lock = threading.RLock()
        self._chunks: Dict[str, dict] = {}          # chunk_id -> 运行时块
        self._corpus_ids: List[str] = []            # BM25 语料对齐的 id 顺序
        self._bm25: Optional[BM25Okapi] = None
        self._dense_indexes = {}  # dim -> (ids, matrix)，只保留当前模型版本，不同维度永不混算
        self._dense_version = ""
        self._query_dimensions = {}  # 从真实查询响应获知的模型维度
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
        vector = None
        if row.embedding:
            try:
                vector = np.frombuffer(row.embedding, dtype=np.float32)
            except ValueError:
                logger.warning("知识块 %s 向量损坏，已跳过；请重建向量", row.id)
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
            "_embedding": vector,
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

    @staticmethod
    def _usable_vector(chunk: dict, version: str) -> bool:
        vector = chunk["_embedding"]
        return bool(version and chunk["embedding_version"] == version and vector is not None
                    and vector.ndim == 1 and vector.size and np.isfinite(vector).all())

    def _rebuild_dense(self, version=None):
        version = embedding_version() if version is None else version
        groups = {}
        for cid, c in self._chunks.items():
            if self._usable_vector(c, version):
                ids, vecs = groups.setdefault(c["_embedding"].size, ([], []))
                ids.append(cid)
                vecs.append(c["_embedding"])
        # 在锁内一次性交换完整快照，查询持有的旧矩阵不会被修改。
        indexes = {dim: (tuple(ids), np.vstack(vecs)) for dim, (ids, vecs) in groups.items()}
        self._dense_indexes, self._dense_version = indexes, version

    # ---------------- 文档管理 ----------------

    def add_document(self, doc_id: str, doc_name: str, file_path: str, chunks: List[dict]) -> int:
        """写入块 + 计算向量 + 重建内存索引。返回入库块数。"""
        if not chunks:
            return 0

        config = embedding_client.snapshot()
        emb_version = config.version
        texts = [c["context_text"] for c in chunks]
        vectors = embedding_client.embed_texts_sync(texts, snapshot=config) if emb_version else None

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
        # 与策展写回共用锁；条目、源块、文档同事务删除，提交后再更新内存索引。
        with self._lock:
            with get_session() as session:
                doc = session.get(KBDocument, doc_id)
                if not doc:
                    return False
                session.exec(sql_delete(KBItem).where(KBItem.doc_id == doc_id))  # type: ignore[arg-type]
                session.exec(sql_delete(KBChunk).where(KBChunk.doc_id == doc_id))  # type: ignore[arg-type]
                session.delete(doc)
            self.reload()
        return True

    def reindex_embeddings(self, progress=None) -> int:
        """
        用固定模型补齐缺失或不兼容的向量，成功后一次提交；失败时继续使用原索引。
        已是当前模型版本且维度一致的块不重新计算；同一版本的输出维度变了（或已有向量维度混杂）时全量重建。
        """
        config = embedding_client.snapshot()
        emb_version = config.version
        if not emb_version:
            return 0
        with self._lock:
            chunks = dict(self._chunks)
            dimension = self._target_dimension(emb_version)
        kept = {cid for cid, c in chunks.items()
                if dimension and self._usable_vector(c, emb_version) and c["_embedding"].size == dimension}
        stale = [cid for cid in chunks if cid not in kept]
        if not stale:
            return 0
        rebuilt = self._embed_chunks(chunks, stale, config, progress)
        new_dimension = next(iter(rebuilt.values())).size
        if kept and new_dimension != dimension:
            # 模型版本未变但输出维度变了：已有向量也不能再与新向量混用
            rebuilt.update(self._embed_chunks(chunks, [cid for cid in chunks if cid in kept], config, progress,
                                              new_dimension, "维度变化，全量重建"))
        if embedding_version() != emb_version:
            raise RuntimeError("嵌入配置已变化，请使用当前模型重新构建向量（原索引保留）")
        done, doc_ids = 0, set()
        with get_session() as session:
            for cid, vector in rebuilt.items():
                row = session.get(KBChunk, cid)
                if row is not None and row.context_text == chunks[cid]["context_text"]:
                    row.embedding, row.embedding_version = vector.tobytes(), emb_version
                    session.add(row)
                    doc_ids.add(row.doc_id)
                    done += 1
            for doc_id in doc_ids:
                doc = session.get(KBDocument, doc_id)
                if doc:
                    doc.embedding_version, doc.updated_at = emb_version, now_str()
                    session.add(doc)
        self.reload()
        with self._lock:
            self._query_dimensions[emb_version] = new_dimension
        return done

    @staticmethod
    def _embed_chunks(chunks: Dict[str, dict], ids: List[str], config, progress=None,
                      dimension: Optional[int] = None, label: str = "向量重建") -> Dict[str, np.ndarray]:
        """按批计算向量；任一批失败或维度不一致即中止（调用方尚未提交，原索引保留）"""
        rebuilt = {}
        for i in range(0, len(ids), REINDEX_BATCH):
            batch_ids = ids[i : i + REINDEX_BATCH]
            vectors = embedding_client.embed_texts_sync([chunks[cid]["context_text"] for cid in batch_ids], snapshot=config)
            if vectors is None:
                raise RuntimeError("嵌入接口调用失败，向量重建中止（原索引保留）")
            if dimension is not None and vectors.shape[1] != dimension:
                raise RuntimeError("嵌入返回维度不一致，向量重建中止（原索引保留）")
            dimension = vectors.shape[1]
            rebuilt.update(zip(batch_ids, vectors))
            if progress:
                progress.report(int(len(rebuilt) / len(ids) * 100), f"{label} {len(rebuilt)}/{len(ids)}")
        return rebuilt

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
        config = embedding_client.snapshot()
        if not config.version:
            return []
        with self._lock:
            if self._dense_version != config.version:
                self._rebuild_dense(config.version)
            indexes, chunks = self._dense_indexes, self._chunks
            if not indexes:
                return []
        # 网络调用在索引锁外，且使用与索引相同的配置快照。
        q = embedding_client.embed_query_sync(query, snapshot=config)
        if q is None or q.ndim != 1 or not q.size or not np.isfinite(q).all():
            return []
        if embedding_version() != config.version:
            return []
        with self._lock:
            self._query_dimensions[config.version] = q.size
        compatible = indexes.get(q.size)
        if compatible is None:
            return []
        ids, matrix = compatible
        q_norm = q / (np.linalg.norm(q) + 1e-10)
        mat = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-10)
        scores = mat @ q_norm
        results = []
        for idx in np.argsort(-scores)[: top_k * 3]:
            cid = ids[int(idx)]
            if doc_filter and chunks[cid]["doc_id"] not in doc_filter:
                continue
            results.append((cid, float(scores[idx])))
            if len(results) >= top_k:
                break
        return results

    def _target_dimension(self, version: str) -> Optional[int]:
        """当前模型的向量维度：以真实查询响应为准；未查询过时，已有向量只有一种维度即取该维度。调用方持锁。"""
        if self._dense_version != version:
            self._rebuild_dense(version)
        dim = self._query_dimensions.get(version)
        if dim is None and len(self._dense_indexes) == 1:
            dim = next(iter(self._dense_indexes))
        return dim

    def dense_status(self) -> dict:
        config = embedding_client.snapshot()
        with self._lock:
            dim = self._target_dimension(config.version)
            compatible = len(self._dense_indexes.get(dim, ((), None))[0])
            stale = len(self._chunks) - compatible if config.version else 0
        message = ""
        if stale:
            message = ("无兼容的向量索引，已退回 BM25 检索，请重建向量" if not compatible else
                       f"{stale} 个知识块的向量与当前模型不兼容或缺失，请重建向量")
        return {"dense_enabled": bool(config.version), "dense_ready": bool(compatible),
                "embedding_model": config.model if config.version else "",
                "embedding_stale_count": stale, "embedding_warning": message}

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

    def write_lock(self) -> threading.RLock:
        """与索引重载、删除文档互斥的可重入锁：策展等在模型调用之后写回知识库的流程持有它确认来源仍存在"""
        return self._lock

    def get_chunk(self, chunk_id: str) -> Optional[dict]:
        with self._lock:
            return self._chunks.get(chunk_id)

    def get_chunks(self, chunk_ids: List[str]) -> List[dict]:
        with self._lock:
            return [self._chunks[cid] for cid in chunk_ids if cid in self._chunks]

    def get_document_chunks(self, doc_id: str) -> List[dict]:
        with self._lock:
            return sorted((c for c in self._chunks.values() if c["doc_id"] == doc_id), key=lambda c: c["seq"])

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
        with self._lock:
            with_embeddings = sum(1 for c in self._chunks.values() if c["_embedding"] is not None)
        return {
            "total_chunks": len(self._chunks),
            "total_documents": len(docs),
            "documents": docs,
            "chunks_with_embeddings": with_embeddings,
            **self.dense_status(),
        }

    def all_tags(self) -> List[str]:
        tags = set()
        for c in self._chunks.values():
            tags.update(c["tags"])
        return sorted(tags)


knowledge_index = KnowledgeIndex()
