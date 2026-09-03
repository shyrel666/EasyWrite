import json
import math
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import jieba

from app.core.config import settings

class HierarchicalVectorStore:
    """
    针对招投标标书特性的层级结构化混合知识库：
    1. 文本与大纲路径混合加权（Breadcrumb + Content）
    2. 中文分词与 TF-IDF / 语义相似度双路召回
    3. 支持标签过滤（Tag-based filtering）
    4. 本地轻量化自持久化，无须繁琐安装外部重型数据库即可即刻运行
    """

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or settings.KNOWLEDGE_DIR
        self.storage_file = self.storage_dir / "knowledge_index.json"
        self.chunks: List[Dict[str, Any]] = []
        self.vectorizer: Optional[TfidfVectorizer] = None
        self.tfidf_matrix = None
        self._load_from_disk()

    def _load_from_disk(self):
        if self.storage_file.exists():
            try:
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.chunks = data.get("chunks", [])
                if self.chunks:
                    self._rebuild_index()
            except Exception as e:
                print(f"加载知识库持久化文件失败: {e}")
                self.chunks = []

    def _save_to_disk(self):
        try:
            with open(self.storage_file, "w", encoding="utf-8") as f:
                json.dump({"chunks": self.chunks}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存知识库失败: {e}")

    def _tokenize_chinese(self, text: str) -> str:
        words = jieba.lcut(text)
        return " ".join(words)

    def _rebuild_index(self):
        """对所有已入库切片重建中文检索索引"""
        if not self.chunks:
            self.vectorizer = None
            self.tfidf_matrix = None
            return

        corpus = []
        for c in self.chunks:
            # 将面包屑路径强化3倍权重放入检索语料
            enriched_text = (c.get("breadcrumb", "") + " ") * 3 + c.get("content", "")
            corpus.append(self._tokenize_chinese(enriched_text))

        self.vectorizer = TfidfVectorizer(token_pattern=r"(?u)\b\w+\b")
        self.tfidf_matrix = self.vectorizer.fit_transform(corpus)

    def add_chunks(self, new_chunks: List[Dict[str, Any]]):
        """批量导入知识切片"""
        existing_ids = {c["id"] for c in self.chunks}
        added_count = 0
        for chunk in new_chunks:
            if chunk["id"] not in existing_ids:
                self.chunks.append(chunk)
                existing_ids.add(chunk["id"])
                added_count += 1

        if added_count > 0:
            self._rebuild_index()
            self._save_to_disk()
        return added_count

    def search(self, query: str, top_k: int = 5, tag_filter: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        混合检索：语义/关键词匹配 + 面包屑大纲加权 + 标签过滤
        """
        if not self.chunks or self.vectorizer is None or self.tfidf_matrix is None:
            return []

        # 1. 标签初步候选筛选
        candidate_indices = list(range(len(self.chunks)))
        if tag_filter:
            tag_set = set(tag_filter)
            candidate_indices = [
                i for i in candidate_indices
                if any(t in tag_set for t in self.chunks[i].get("tags", []))
            ]
            if not candidate_indices:
                # 若完全无匹配，放宽限制
                candidate_indices = list(range(len(self.chunks)))

        # 2. 计算相似度
        tokenized_query = self._tokenize_chinese(query)
        try:
            query_vec = self.vectorizer.transform([tokenized_query])
            sim_scores = cosine_similarity(query_vec, self.tfidf_matrix)[0]
        except Exception:
            sim_scores = np.zeros(len(self.chunks))

        # 3. 结合面包屑路径与标题完全匹配度加权
        results = []
        for idx in candidate_indices:
            chunk = self.chunks[idx]
            base_score = float(sim_scores[idx])

            # 面包屑关键词加权
            path_bonus = 0.0
            for term in query.split():
                if term and (term in chunk.get("breadcrumb", "") or term in chunk.get("section_title", "")):
                    path_bonus += 0.2

            final_score = base_score + path_bonus
            
            # 拷贝避免修改原始对象
            res_item = dict(chunk)
            res_item["score"] = round(final_score, 4)
            results.append(res_item)

        # 按相似度降序排序
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def clear(self):
        self.chunks = []
        self.vectorizer = None
        self.tfidf_matrix = None
        if self.storage_file.exists():
            self.storage_file.unlink()

# 单例管理
knowledge_store = HierarchicalVectorStore()
