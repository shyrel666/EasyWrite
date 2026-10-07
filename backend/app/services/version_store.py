"""
章节历史版本仓储（覆盖安全网）。

record() 在 AI 生成 / 润色 / 批量撰写 / 恢复 **写回成功之后** 调用：
- 覆盖前的正文若与最新版本不同（多为人工编辑稿），先存一版 source=manual
- 再存新正文一版（source 为本次操作）
不在 project_store.update() 的事务内写入：SQLite WAL 下读事务中途让别的连接提交会导致写冲突。
"""
import logging
import re
from datetime import datetime
from typing import List, Optional

from sqlmodel import delete, select

from app.db.database import get_session
from app.db.models import SectionVersion

logger = logging.getLogger("easywrite.versions")

MAX_VERSIONS_PER_SECTION = 30


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _chars(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


class VersionStore:
    def record(self, project_id: str, section_id: str, old_content: str, new_content: str, source: str):
        try:
            with get_session() as session:
                latest = session.exec(
                    select(SectionVersion)
                    .where(SectionVersion.project_id == project_id, SectionVersion.section_id == section_id)
                    .order_by(SectionVersion.id.desc())  # type: ignore[union-attr]
                ).first()
                latest_content = latest.content if latest else None
                if (old_content or "").strip() and old_content != latest_content and old_content != new_content:
                    session.add(self._make(project_id, section_id, old_content, "manual"))
                if (new_content or "").strip() and new_content != old_content:
                    session.add(self._make(project_id, section_id, new_content, source))
            self._prune(project_id, section_id)
        except Exception:  # 版本是安全网，失败不影响正文保存
            logger.exception("记录章节版本失败 %s/%s", project_id, section_id)

    @staticmethod
    def _make(project_id: str, section_id: str, content: str, source: str) -> SectionVersion:
        return SectionVersion(project_id=project_id, section_id=section_id, content=content,
                              source=source, char_count=_chars(content), created_at=_now())

    @staticmethod
    def _prune(project_id: str, section_id: str):
        with get_session() as session:
            ids = session.exec(
                select(SectionVersion.id)
                .where(SectionVersion.project_id == project_id, SectionVersion.section_id == section_id)
                .order_by(SectionVersion.id.desc())  # type: ignore[union-attr]
            ).all()
            stale = ids[MAX_VERSIONS_PER_SECTION:]
            if stale:
                session.exec(delete(SectionVersion).where(SectionVersion.id.in_(stale)))  # type: ignore[union-attr]

    def list(self, project_id: str, section_id: str) -> List[dict]:
        with get_session() as session:
            rows = session.exec(
                select(SectionVersion)
                .where(SectionVersion.project_id == project_id, SectionVersion.section_id == section_id)
                .order_by(SectionVersion.id.desc())  # type: ignore[union-attr]
            ).all()
            return [{"id": r.id, "source": r.source, "char_count": r.char_count, "created_at": r.created_at,
                     "preview": re.sub(r"\s+", " ", r.content)[:80]} for r in rows]

    def get(self, project_id: str, section_id: str, version_id: int) -> Optional[SectionVersion]:
        with get_session() as session:
            row = session.get(SectionVersion, version_id)
            if row and row.project_id == project_id and row.section_id == section_id:
                session.expunge(row)
                return row
            return None

    def delete_project(self, project_id: str):
        with get_session() as session:
            session.exec(delete(SectionVersion).where(SectionVersion.project_id == project_id))


version_store = VersionStore()
