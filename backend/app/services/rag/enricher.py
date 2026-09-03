"""
上下文增强器（可选 LLM 摘要）。

Anthropic Contextual Retrieval 的完整形态：为每个分块生成一句话"本块在讲什么、
对标书写作有什么用"的定位摘要，前置到嵌入输入中，显著提升检索命中率
（官方基准：top-20 失败率 -35%，叠加 BM25 -49%，再叠加重排 -67%）。

成本控制：默认关闭（面包屑增强已可用），在 AI 设置中开启后按批调用。
"""
import logging
from typing import Dict, List

from app.core.config import settings
from app.core.llm_client import llm_client
from app.services.rag.chunker import _build_context_text

logger = logging.getLogger("easywrite.rag.enricher")

BATCH = 10

_ENRICH_SYSTEM = (
    "你是投标资料知识库的索引助手。给定一批来自同一份历史标书/方案文档的文本块，"
    "为每个块写一句话定位摘要（不超过 40 字）：说明该块的核心内容与对撰写技术标书的参考价值。"
    '只输出 JSON：{"summaries": ["摘要1", "摘要2", ...]}，数组长度必须与输入块数一致。'
)


def enrich_chunks_inplace(doc_name: str, chunks: List[Dict], progress=None) -> int:
    """
    为分块批量生成 LLM 定位摘要并重写 context_text。
    返回成功增强的块数；LLM 不可用时返回 0（保持面包屑增强）。
    """
    if not settings.RAG_CONTEXTUAL_SUMMARIES:
        return 0
    if not llm_client.is_configured:
        return 0

    enriched = 0
    total = len(chunks)
    for i in range(0, total, BATCH):
        batch = chunks[i : i + BATCH]
        lines = []
        for j, c in enumerate(batch):
            head = c["content"][:300]
            lines.append(f"[块{j + 1}] 面包屑：{c['breadcrumb']}\n{head}")
        user_prompt = "文档名：" + doc_name + "\n\n" + "\n\n".join(lines)

        result = llm_client.chat_completion_structured(_ENRICH_SYSTEM, user_prompt, temperature=0.1)
        summaries = None
        if isinstance(result, dict) and isinstance(result.get("summaries"), list):
            summaries = [str(s) for s in result["summaries"]]

        if summaries:
            for j, c in enumerate(batch):
                summary = summaries[j].strip() if j < len(summaries) else ""
                if summary:
                    c["context_text"] = _build_context_text(doc_name, c["breadcrumb"], c["content"], summary)
                    c["llm_summary"] = summary
                    enriched += 1
        if progress:
            progress.report(int((min(i + BATCH, total)) / max(total, 1) * 100), f"LLM 摘要增强 {min(i + BATCH, total)}/{total}")

    logger.info("上下文增强完成：%d/%d 块", enriched, total)
    return enriched
