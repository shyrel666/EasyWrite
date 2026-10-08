"""
章节正文写入（唯一入口）：人工保存、AI 生成、润色、批量撰写、恢复、偏离表回填、采纳候选稿都经此写回。

- 写锁内重新读取最新项目再修改单个章节，不覆盖其他章节的并发编辑
- 正文变化时修订号 revision 加 1，并记录来源 content_source（候选稿采纳时据修订号判断正文是否变化）
- version_source 非空的覆盖操作在写回正文的同一事务内记录历史版本；版本写入失败时正文一并回滚
"""
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from sqlmodel import Session

from app.models.schemas import OutlineNode, Project
from app.services.project_store import find_node, project_store
from app.services.version_store import version_store

# 正文来源：manual 人工编辑 / ai_generate 单章 AI 撰写 / batch 批量撰写 / polish 润色 / restore 恢复历史版本 /
# proposal 采纳候选稿 / deviation 偏离表回填
CONTENT_SOURCES = ("manual", "ai_generate", "batch", "polish", "restore", "proposal", "deviation")


def write_node(node: OutlineNode, content: str, status: str, source: str) -> str:
    """在 mutate 内修改章节正文：正文变化时修订号加 1 并记录来源；返回覆盖前的正文"""
    old = node.content
    if content != old:
        node.revision += 1
        node.content_source = source
    node.content = content
    node.status = status
    return old


def save_section_content(
    project_id: str, section_id: str, content: str, status: str,
    last_refs: Optional[List[dict]] = None,
    version_source: Optional[str] = None,
    expect_content: Optional[str] = None,
    content_source: Optional[str] = None,
    check: Optional[Callable[[Project, OutlineNode], None]] = None,
    also: Optional[Callable[[Session, Dict[str, Any]], None]] = None,
    **node_updates: Any,
) -> Optional[str]:
    """
    原子写回单个章节，返回写入后的状态。
    version_source：AI 生成/润色/批量/恢复等覆盖操作，在写回正文的同一事务内记录历史版本；
    版本写入失败时正文一并回滚并抛出异常（人工自动保存不传，不留版）。
    content_source：正文来源，缺省取 version_source，再缺省为 manual。
    expect_content：仅当章节正文仍等于该值时才写入（批量撰写期间用户改过的章节不覆盖），否则返回 None。
    check(project, node)：写入前在同一写锁内校验（抛 HTTPException 则不写入）。
    also(session, info)：同一事务内的附加写入（info 含 old/revision），抛出异常时整体回滚。
    node_updates：一并修改的章节字段（如 content_mode）。
    """
    source = content_source or version_source or "manual"

    def mutate(project: Project):
        node = find_node(project.outline, section_id)
        if not node:
            raise HTTPException(status_code=404, detail="未找到对应章节")
        if expect_content is not None and node.content != expect_content:
            return None
        if check is not None:
            check(project, node)
        old = write_node(node, content, status, source)
        if last_refs is not None:
            node.last_refs = last_refs
        for key, value in node_updates.items():
            setattr(node, key, value)
        if project.stage != "writing":
            project.stage = "writing"
        return {"status": node.status, "old": old, "revision": node.revision}

    def extra(session, info):
        if info is None:
            return
        if version_source:
            version_store.record_in(session, project_id, section_id, info["old"] or "", content, version_source)
        if also is not None:
            also(session, info)

    info = project_store.update(project_id, mutate, also=extra)
    if info is not None and version_source:
        version_store.prune(project_id, section_id)
    return info["status"] if info is not None else None
