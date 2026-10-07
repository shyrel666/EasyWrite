"""
分层混合检索服务（RAG 检索管线）。

流程：多查询构造 → BM25 ∥ 稠密 双路召回 → RRF 融合 → LLM 列表重排
     → 分数阈值（宁缺毋滥：低于阈值明确返回"无高置信参考"）
     → 父级上下文扩展 → 引用溯源（含锁定/排除人工在环）

核心原则（源自 OpenBidKit 作者论证）：标书写作中，错误的参考比没有参考更糟。
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.core.config import settings
from app.core.llm_client import llm_client
from app.services.rag.indexer import knowledge_index

logger = logging.getLogger("easywrite.rag.retriever")


@dataclass
class RetrievedRef:
    chunk_id: str
    doc_id: str
    doc_name: str
    section_title: str
    breadcrumb: str
    content: str
    tags: List[str] = field(default_factory=list)
    # 各阶段分数
    bm25_score: float = 0.0
    dense_score: float = 0.0
    rrf_score: float = 0.0
    rerank_score: Optional[float] = None
    rerank_reason: str = ""
    pinned: bool = False

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "doc_name": self.doc_name,
            "section_title": self.section_title,
            "breadcrumb": self.breadcrumb,
            "content": self.content,
            "tags": self.tags,
            "bm25_score": round(self.bm25_score, 4),
            "dense_score": round(self.dense_score, 4),
            "rrf_score": round(self.rrf_score, 5),
            "rerank_score": self.rerank_score,
            "rerank_reason": self.rerank_reason,
            "pinned": self.pinned,
        }


_RERANK_SYSTEM = (
    "你是标书知识库检索的重排专家。给定写作需求与候选参考资料列表，"
    "为每条资料评估其对撰写该章节正文的实际参考价值，打 0-10 分：\n"
    "9-10=直接可用的成熟方案/同领域实施细节；6-8=相关可借鉴；3-5=弱相关；"
    "0-2=无关或跨领域误导（如消防工程资料对装修项目是错误参考）。\n"
    '只输出 JSON：{"scores": [{"id": "...", "score": 8, "reason": "不超过30字"}]}'
)


class RetrievalService:
    def retrieve(
        self,
        query: str,
        section_title: str = "",
        section_path: str = "",
        requirements: Optional[List[str]] = None,
        project_context: str = "",
        top_k: int = 5,
        rerank: bool = True,
        pinned_ids: Optional[List[str]] = None,
        excluded_ids: Optional[List[str]] = None,
        doc_filter: Optional[List[str]] = None,
    ) -> Dict:
        """
        执行完整检索管线。

        返回：{refs: [...], mode, recall_count, fused_count, reranked: bool,
               threshold: float, message: str}
        """
        pinned_ids = pinned_ids or []
        excluded_ids = set(excluded_ids or [])

        # ---- 1. 多查询构造 ----
        queries = [query]
        if section_title and section_title not in query:
            breadcrumb_part = f"{section_path} {section_title}".strip()
            queries.append(f"{breadcrumb_part} {query}"[:512])
        if requirements:
            kw = " ".join(requirements[:6])
            queries.append(f"{section_title} {kw}"[:512])

        # ---- 2. 双路召回 ----
        recall_k = settings.RAG_RECALL_TOP_K
        bm25_lists, dense_lists = [], []
        for q in queries:
            bm25_lists.append(knowledge_index.search_bm25(q, top_k=recall_k, doc_filter=doc_filter))
            dense_lists.append(knowledge_index.search_dense(q, top_k=recall_k, doc_filter=doc_filter))

        bm25_map = {cid: s for lst in bm25_lists for cid, s in lst}
        dense_map = {cid: s for lst in dense_lists for cid, s in lst}

        # ---- 3. RRF 融合 ----
        fused = knowledge_index.fuse_rrf(bm25_lists + dense_lists)

        # 锁定引用绕过阈值直接入选（人工在环：用户明确要用的资料）
        candidate_ids = [cid for cid, _ in fused if cid not in excluded_ids]
        pinned_present = [cid for cid in pinned_ids if knowledge_index.get_chunk(cid) and cid not in excluded_ids]

        reranked = False
        final_scored: List[tuple] = []

        # ---- 4. LLM 列表重排 ----
        candidates = candidate_ids[: max(top_k * 4, 12)]
        if rerank and llm_client.is_configured and candidates:
            scored = self._llm_rerank(queries[0], section_title, candidates)
            if scored is not None:
                reranked = True
                threshold = settings.RAG_RERANK_THRESHOLD
                kept = [(cid, s, r) for cid, s, r in scored if s >= threshold]
                # 锁定块始终保留
                kept_ids = {cid for cid, _, _ in kept}
                for pid in pinned_present:
                    if pid not in kept_ids:
                        kept.append((pid, 10.0, "用户锁定"))
                final_scored = sorted(kept, key=lambda x: -x[1])[:top_k]
        if not final_scored and not reranked:
            # 无 LLM（或重排失败）：退化为 RRF 排名直取（BM25 已提供语义兜底的关键词精度）
            final_scored = [(cid, rrf, "") for cid, rrf in fused if cid not in excluded_ids][:top_k]
            for pid in pinned_present:
                if not any(cid == pid for cid, _, _ in final_scored):
                    final_scored.insert(0, (pid, 10.0, "用户锁定"))
            final_scored = final_scored[:top_k]

        # ---- 5. 组装引用（含溯源与父级上下文） ----
        refs: List[RetrievedRef] = []
        for cid, score, reason in final_scored:
            chunk = knowledge_index.get_chunk(cid)
            if not chunk:
                continue
            ref = RetrievedRef(
                chunk_id=cid,
                doc_id=chunk["doc_id"],
                doc_name=knowledge_index.get_doc_name(chunk["doc_id"]),
                section_title=chunk["section_title"],
                breadcrumb=chunk["breadcrumb"],
                content=self._compose_content(chunk),
                tags=chunk["tags"],
                bm25_score=bm25_map.get(cid, 0.0),
                dense_score=dense_map.get(cid, 0.0),
                rrf_score=dict(fused).get(cid, 0.0),
                rerank_score=score if reranked else None,
                rerank_reason=reason,
                pinned=cid in pinned_ids,
            )
            refs.append(ref)

        message = ""
        if not refs:
            message = "知识库中无高置信匹配参考，本次生成将不注入历史资料（错误参考不如不引用）"
        elif not reranked:
            message = "未启用 LLM 重排（未配置模型），当前为 BM25/混合召回排序"

        return {
            "refs": [r.to_dict() for r in refs],
            "mode": llm_client.get_mode(),
            "recall_count": len(bm25_map) + len(dense_map),
            "fused_count": len(fused),
            "reranked": reranked,
            "threshold": settings.RAG_RERANK_THRESHOLD if reranked else None,
            "message": message,
        }

    def _llm_rerank(self, query: str, section_title: str, candidate_ids: List[str]):
        """LLM 列表重排：对候选打 0-10 分。失败返回 None（调用方退化处理）。"""
        from app.services.rag.indexer import knowledge_index as idx

        lines = []
        for i, cid in enumerate(candidate_ids):
            c = idx.get_chunk(cid)
            if not c:
                continue
            head = c["content"][:220].replace("\n", " ")
            lines.append(f"[{i + 1}] id={cid} 来源：{c['breadcrumb']}\n内容：{head}")
        if not lines:
            return None

        user_prompt = (
            f"写作需求：{section_title or ''} {query}\n\n候选资料：\n" + "\n\n".join(lines)
        )
        result = llm_client.chat_completion_structured(_RERANK_SYSTEM, user_prompt, temperature=0.1, purpose="rerank")
        if not isinstance(result, dict) or not isinstance(result.get("scores"), list):
            return None
        id_list = [cid for cid in candidate_ids if idx.get_chunk(cid)]
        scored = []
        for item in result["scores"]:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id", ""))
            score = item.get("score")
            if cid in id_list and isinstance(score, (int, float)):
                scored.append((cid, float(score), str(item.get("reason", ""))[:60]))
        if not scored:
            return None
        return scored

    @staticmethod
    def _compose_content(chunk: dict) -> str:
        """命中块正文 + 父级上下文扩展（父章节摘要帮助模型理解块的位置语境）"""
        content = chunk["content"]
        parent = chunk.get("parent_summary", "")
        if parent and parent not in content:
            return f"（所属章节背景：{parent[:180]}）\n{content}"
        return content

    def build_reference_prompt(self, refs: List[dict]) -> str:
        """将命中引用组装为生成提示词中的参考资料块（附改写使用规则）"""
        if not refs:
            return ""
        blocks = []
        for i, r in enumerate(refs, 1):
            blocks.append(
                f"<参考资料{i} 来源=\"{r.get('doc_name', '')} / {r.get('breadcrumb', '')}\">\n"
                f"{r.get('content', '')}\n</参考资料{i}>"
            )
        return (
            "以下为从企业历史标书知识库检索到的参考资料。使用规则：\n"
            "1. 只吸收其方案思路、技术参数与实施经验，必须结合本项目语境改写，严禁整段照抄；\n"
            "2. 标书正文中绝对不得出现\"知识库\"\"历史文档\"\"参考资料\"等来源字样；\n"
            "3. 若资料与本项目领域不符，宁可不用，不得强行套用。\n\n" + "\n\n".join(blocks)
        )


retrieval_service = RetrievalService()
