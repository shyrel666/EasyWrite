"""
LLM 知识条目策展（FB208 式，大纲阶段路由层）。

入库后（可选）由 LLM 把整份文档提炼为"知识条目"：
  {title: 条目标题, summary: 对标书写作的用途说明, chunk_ids: 源文本块区间}
- 两轮提取（初提 + 补漏）保证召回
- 条目内容 = 关联块原文拼接（溯源、不转述）
- 大纲规划时只向 LLM 展示条目元数据（title + summary），
  由 LLM 自选相关条目——"技能注册表"式路由，避免向量检索的跨领域误召回
"""
import json
import logging
from datetime import datetime
from typing import Dict, List

from sqlmodel import select, delete as sql_delete

from app.core.llm_client import llm_client
from app.db.database import get_session
from app.db.models import KBItem, KBDocument
from app.services.rag.indexer import knowledge_index

logger = logging.getLogger("easywrite.rag.curator")

_EXTRACT_SYSTEM = (
    "你是投标资料知识库分析助手。给定一份历史标书/方案文档的文本块集合，"
    "提炼出对撰写技术标书有复用价值的知识条目，覆盖：技术方案、项目管理、质量管理、"
    "安全保密、进度计划、售后服务、应急预案、人员设备、同类业绩等维度。\n"
    "每个条目：title 为简洁主题名（不超过 20 字）；summary 说明该条目如何辅助标书写作（不超过 60 字）；"
    "chunk_ids 为支撑该条目的块 id 闭区间列表（只使用输入中出现的 id）。\n"
    '只输出 JSON：{"items": [{"title": "", "summary": "", "chunk_ids": ["id1", "id2"]}]}'
)

_SUPPLEMENT_SYSTEM = (
    "你是投标资料知识库的查漏助手。以下是第一轮已提取的知识条目与文档全部块列表。"
    "请检查是否有明显遗漏的可复用知识主题（如部署架构、数据治理、信创适配等）。"
    '没有遗漏是正常结果。只输出 JSON：{"items": []}，格式同第一轮。'
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def curate_document(doc_id: str, progress=None) -> int:
    """
    对指定文档执行 LLM 条目策展（后台任务入口）。
    返回写入的条目数；LLM 不可用返回 0。
    """
    chunks = knowledge_index.get_document_chunks(doc_id)
    if not chunks:
        return 0
    if not llm_client.is_configured:
        logger.info("LLM 未配置，跳过条目策展（doc=%s）", doc_id)
        return 0

    doc_name = knowledge_index.get_doc_name(doc_id)
    progress and progress.report(10, "第一轮知识条目提取")

    block_listing = "\n".join(
        f"[{c['id']}] {c['breadcrumb']}：{c['content'][:150].replace(chr(10), ' ')}"
        for c in chunks
    )
    user_prompt = f"文档名：{doc_name}\n\n文本块集合：\n{block_listing}"

    items: List[Dict] = []
    first = llm_client.chat_completion_structured(_EXTRACT_SYSTEM, user_prompt, temperature=0.2, purpose="kb_curate")
    if isinstance(first, dict) and isinstance(first.get("items"), list):
        items.extend(i for i in first["items"] if isinstance(i, dict) and i.get("title"))

    progress and progress.report(50, "第二轮查漏提取")
    titles = "\n".join(f"- {i.get('title', '')}" for i in items)
    second = llm_client.chat_completion_structured(
        _SUPPLEMENT_SYSTEM,
        f"文档名：{doc_name}\n\n第一轮已提取条目：\n{titles}\n\n文本块集合：\n{block_listing}",
        temperature=0.2,
        purpose="kb_curate",
    )
    if isinstance(second, dict) and isinstance(second.get("items"), list):
        items.extend(i for i in second["items"] if isinstance(i, dict) and i.get("title"))

    # 标题去重合并
    seen = {}
    for i in items:
        key = str(i.get("title", "")).replace(" ", "").lower()
        if key and key not in seen:
            seen[key] = i
        elif key in seen:
            prev_ids = set(seen[key].get("chunk_ids", []) or [])
            prev_ids.update(i.get("chunk_ids", []) or [])
            seen[key]["chunk_ids"] = sorted(prev_ids)

    progress and progress.report(80, f"写入 {len(seen)} 条知识条目")

    valid_ids = {c["id"] for c in chunks}
    # 模型调用不持锁。写回前确认文档仍存在，防止删除期间的策展重新制造孤立条目。
    with knowledge_index.write_lock(), get_session() as session:
        doc = session.get(KBDocument, doc_id)
        if doc is None:
            return 0
        session.exec(sql_delete(KBItem).where(KBItem.doc_id == doc_id))  # type: ignore[arg-type]
        count = 0
        for idx, item in enumerate(seen.values()):
            chunk_ids = [cid for cid in (item.get("chunk_ids") or []) if cid in valid_ids]
            if not chunk_ids:
                # 无块区间的条目按标题关键词 BM25 兜底匹配
                hits = knowledge_index.search_bm25(str(item.get("title", "")), top_k=3, doc_filter=[doc_id])
                chunk_ids = [cid for cid, _ in hits]
                if not chunk_ids:
                    continue
            session.add(KBItem(
                id=f"{doc_id}_it{idx}",
                doc_id=doc_id,
                title=str(item.get("title", ""))[:60],
                summary=str(item.get("summary", ""))[:200],
                chunk_ids_json=json.dumps(chunk_ids, ensure_ascii=False),
                created_at=_now(),
            ))
            count += 1

        doc.item_count = count
        doc.updated_at = _now()
        session.add(doc)

    progress and progress.report(100, f"策展完成：{count} 条")
    return count


def list_items(doc_id: str = None) -> List[dict]:
    """列出知识条目（供大纲路由与知识库浏览）"""
    with get_session() as session:
        stmt = select(KBItem).join(KBDocument, KBItem.doc_id == KBDocument.id)
        if doc_id:
            stmt = stmt.where(KBItem.doc_id == doc_id)
        rows = session.exec(stmt).all()
        return [
            {
                "id": r.id, "doc_id": r.doc_id, "title": r.title, "summary": r.summary,
                "chunk_ids": json.loads(r.chunk_ids_json or "[]"),
            }
            for r in rows
        ]


def build_item_metadata_prompt(doc_name: str = None) -> str:
    """
    大纲规划用：把全部条目元数据渲染成提示词（不含全文，由 LLM 自选）。
    """
    items = list_items(doc_name)
    if not items:
        return ""
    lines = [f"- 条目 {it['id']}：{it['title']}（{it['summary']}）" for it in items]
    return (
        "企业知识库已提炼以下可复用知识条目（仅元数据）。请在大纲设计中评估哪些条目"
        "可支撑对应章节的撰写，并在相关章节的 requirements 中标注条目 id：\n" + "\n".join(lines)
    )


def get_item_contents(item_ids: List[str]) -> List[dict]:
    """按条目 id 取全文（源块原文拼接，溯源不转述）"""
    with get_session() as session:
        result = []
        for iid in item_ids:
            item = session.exec(select(KBItem).join(KBDocument, KBItem.doc_id == KBDocument.id)
                                .where(KBItem.id == iid)).first()
            if not item:
                continue
            chunks = knowledge_index.get_chunks(json.loads(item.chunk_ids_json or "[]"))
            content = "\n\n".join(c["content"] for c in chunks)
            result.append({
                "id": item.id, "title": item.title, "summary": item.summary,
                "content": content,
                "sources": [c["breadcrumb"] for c in chunks],
            })
        return result
