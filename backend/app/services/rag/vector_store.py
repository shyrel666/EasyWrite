import json
import math
import os
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import jieba
from openai import OpenAI

from app.core.config import settings
from app.core.ai_settings_manager import ai_settings_manager

class HierarchicalVectorStore:
    """
    标书层级结构化混合知识库（升级版：Dense 神经向量 + Sparse TF-IDF 混合检索）：
    1. 文本与大纲路径混合加权（Breadcrumb + Content）
    2. 支持调用 OpenAI / SiliconFlow / DashScope 兼容的 Dense Embedding API
    3. 本地 TF-IDF + Jieba 分词自适应降级兜底
    4. 标签过滤与自动持久化
    """

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or settings.KNOWLEDGE_DIR
        self.storage_file = self.storage_dir / "knowledge_index.json"
        self.dense_file = self.storage_dir / "dense_embeddings.npy"
        
        self.chunks: List[Dict[str, Any]] = []
        self.vectorizer: Optional[TfidfVectorizer] = None
        self.tfidf_matrix = None
        self.dense_matrix: Optional[np.ndarray] = None
        
        self._load_from_disk()

    def _get_embedding_client(self) -> Tuple[Optional[OpenAI], str]:
        """获取 Embedding 客户端与目标模型名称"""
        cfg = ai_settings_manager.data
        emb_key = cfg.get("embedding_api_key") or cfg.get("api_key")
        emb_url = cfg.get("embedding_base_url") or cfg.get("base_url")
        emb_model = cfg.get("embedding_model") or "BAAI/bge-large-zh-v1.5"

        if emb_key and not emb_key.startswith("sk-placeholder") and emb_url:
            try:
                client = OpenAI(api_key=emb_key, base_url=emb_url, timeout=15.0)
                return client, emb_model
            except Exception as e:
                print(f"[VectorStore] 初始化 Embedding 客户端失败: {e}")
        return None, ""

    def _load_from_disk(self):
        if self.storage_file.exists():
            try:
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.chunks = data.get("chunks", [])
                if self.dense_file.exists():
                    try:
                        self.dense_matrix = np.load(str(self.dense_file))
                    except Exception:
                        self.dense_matrix = None
                if self.chunks:
                    self._rebuild_index()
            except Exception as e:
                print(f"[VectorStore] 加载知识库持久化文件失败: {e}")
                self.chunks = []

    def _save_to_disk(self):
        try:
            with open(self.storage_file, "w", encoding="utf-8") as f:
                json.dump({"chunks": self.chunks}, f, ensure_ascii=False, indent=2)
            if self.dense_matrix is not None:
                np.save(str(self.dense_file), self.dense_matrix)
        except Exception as e:
            print(f"[VectorStore] 保存知识库失败: {e}")

    def _tokenize_chinese(self, text: str) -> str:
        words = jieba.lcut(text)
        return " ".join(words)

    def _rebuild_index(self):
        """对所有已入库切片重建中文检索索引"""
        if not self.chunks:
            self.vectorizer = None
            self.tfidf_matrix = None
            self.dense_matrix = None
            return

        corpus = []
        for c in self.chunks:
            enriched_text = (c.get("breadcrumb", "") + " ") * 3 + c.get("content", "")
            corpus.append(self._tokenize_chinese(enriched_text))

        self.vectorizer = TfidfVectorizer(token_pattern=r"(?u)\b\w+\b")
        self.tfidf_matrix = self.vectorizer.fit_transform(corpus)

    def _compute_dense_embeddings(self, texts: List[str]) -> Optional[np.ndarray]:
        """批量计算 Dense 语义向量"""
        client, model = self._get_embedding_client()
        if not client or not model:
            return None
        try:
            # 限制单次批处理
            clean_texts = [t[:1000] for t in texts]
            resp = client.embeddings.create(input=clean_texts, model=model)
            vectors = [item.embedding for item in resp.data]
            return np.array(vectors, dtype=np.float32)
        except Exception as e:
            print(f"[VectorStore] Dense Embedding 批量计算失败: {e}")
            return None

    def add_chunks(self, new_chunks: List[Dict[str, Any]]):
        """批量导入知识切片"""
        existing_ids = {c["id"] for c in self.chunks}
        added_chunks = []
        for chunk in new_chunks:
            if chunk["id"] not in existing_ids:
                self.chunks.append(chunk)
                existing_ids.add(chunk["id"])
                added_chunks.append(chunk)

        if added_chunks:
            self._rebuild_index()
            # 尝试计算新增切片的 Dense 向量
            texts_to_embed = [(c.get("breadcrumb", "") + " " + c.get("content", "")) for c in added_chunks]
            new_dense = self._compute_dense_embeddings(texts_to_embed)
            if new_dense is not None:
                if self.dense_matrix is None or len(self.dense_matrix) == 0:
                    self.dense_matrix = new_dense
                else:
                    self.dense_matrix = np.vstack([self.dense_matrix, new_dense])

            self._save_to_disk()
        return len(added_chunks)

    def search(self, query: str, top_k: int = 5, tag_filter: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        混合加权检索：Dense 语义相似度 + TF-IDF 关键词匹配 + 面包屑大纲加权 + 标签过滤
        """
        if not self.chunks or self.vectorizer is None or self.tfidf_matrix is None:
            return []

        # 1. 标签候选筛选
        candidate_indices = list(range(len(self.chunks)))
        if tag_filter:
            tag_set = set(tag_filter)
            candidate_indices = [
                i for i in candidate_indices
                if any(t in tag_set for t in self.chunks[i].get("tags", []))
            ]
            if not candidate_indices:
                candidate_indices = list(range(len(self.chunks)))

        # 2. 计算 Sparse TF-IDF 相似度
        tokenized_query = self._tokenize_chinese(query)
        try:
            query_vec = self.vectorizer.transform([tokenized_query])
            tfidf_scores = cosine_similarity(query_vec, self.tfidf_matrix)[0]
        except Exception:
            tfidf_scores = np.zeros(len(self.chunks))

        # 3. 尝试计算 Dense 语义相似度
        dense_scores = None
        if self.dense_matrix is not None and len(self.dense_matrix) == len(self.chunks):
            client, model = self._get_embedding_client()
            if client and model:
                try:
                    q_dense = client.embeddings.create(input=[query[:1000]], model=model).data[0].embedding
                    q_dense_arr = np.array(q_dense, dtype=np.float32).reshape(1, -1)
                    dense_scores = cosine_similarity(q_dense_arr, self.dense_matrix)[0]
                except Exception as e:
                    dense_scores = None

        # 4. 结合评分加权
        results = []
        for idx in candidate_indices:
            chunk = self.chunks[idx]
            s_sparse = float(tfidf_scores[idx])
            s_dense = float(dense_scores[idx]) if dense_scores is not None else s_sparse

            # 混合打分：若有 Dense 向量，采用 0.6 Dense + 0.4 Sparse
            if dense_scores is not None:
                base_score = 0.6 * s_dense + 0.4 * s_sparse
            else:
                base_score = s_sparse

            # 面包屑关键词命中加权
            path_bonus = 0.0
            for term in query.split():
                if term and (term in chunk.get("breadcrumb", "") or term in chunk.get("section_title", "")):
                    path_bonus += 0.2

            final_score = base_score + path_bonus
            
            res_item = dict(chunk)
            res_item["score"] = round(final_score, 4)
            results.append(res_item)

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def clear(self):
        self.chunks = []
        self.vectorizer = None
        self.tfidf_matrix = None
        self.dense_matrix = None
        if self.storage_file.exists():
            self.storage_file.unlink()
        if self.dense_file.exists():
            self.dense_file.unlink()

knowledge_store = HierarchicalVectorStore()
