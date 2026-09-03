"""
项目仓储层：SQLite 持久化与 Pydantic 领域模型之间的转换。

替代原 endpoints.py 中的内存 dict + 全量重写 JSON 文件方案：
- 每次变更单行 UPDATE，WAL 模式并发安全
- 大纲树/事实/偏离矩阵序列化为 JSON 列（访问模式总是整树加载）
"""
import json
import logging
import uuid
from datetime import datetime
from typing import List, Optional

from sqlmodel import select

from app.db.database import get_session
from app.db.models import ProjectModel
from app.models.schemas import (
    Project, ProjectCreate, ProjectListItem, GlobalFacts,
    TenderAnalysis18, DeviationItem, OutlineNode,
)

logger = logging.getLogger("easywrite.project_store")

# 招标正文存储上限（防超大文件拖垮单行读写）
TENDER_TEXT_LIMIT = 200_000


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class ProjectStore:
    @staticmethod
    def _to_domain(row: ProjectModel) -> Project:
        return Project(
            id=row.id,
            name=row.name,
            client_name=row.client_name,
            description=row.description,
            facts=GlobalFacts(**json.loads(row.facts_json or "{}")),
            tender_analysis=(
                TenderAnalysis18(**json.loads(row.tender_analysis_json))
                if row.tender_analysis_json else None
            ),
            deviation_matrix=[
                DeviationItem(**d) for d in json.loads(row.deviation_json or "[]")
            ],
            outline=[OutlineNode(**n) for n in json.loads(row.outline_json or "[]")],
            stage=row.stage,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _apply_domain(row: ProjectModel, project: Project):
        row.name = project.name
        row.client_name = project.client_name
        row.description = project.description
        row.facts_json = project.facts.model_dump_json()
        row.tender_analysis_json = (
            project.tender_analysis.model_dump_json() if project.tender_analysis else None
        )
        row.deviation_json = json.dumps(
            [d.model_dump() for d in project.deviation_matrix], ensure_ascii=False
        )
        row.outline_json = json.dumps(
            [n.model_dump() for n in project.outline], ensure_ascii=False
        )
        row.stage = project.stage
        row.updated_at = now_str()

    # ---------------- CRUD ----------------

    def get(self, project_id: str) -> Optional[Project]:
        with get_session() as session:
            row = session.get(ProjectModel, project_id)
            return self._to_domain(row) if row else None

    def get_tender_text(self, project_id: str) -> str:
        with get_session() as session:
            row = session.get(ProjectModel, project_id)
            return row.tender_text if row else ""

    def set_tender_text(self, project_id: str, text: str):
        with get_session() as session:
            row = session.get(ProjectModel, project_id)
            if row:
                row.tender_text = (text or "")[:TENDER_TEXT_LIMIT]
                row.updated_at = now_str()
                session.add(row)

    def list(self) -> List[ProjectListItem]:
        with get_session() as session:
            rows = session.exec(
                select(ProjectModel).where(ProjectModel.status == "active").order_by(ProjectModel.updated_at.desc())  # type: ignore[union-attr]
            ).all()
            items = []
            for row in rows:
                p = self._to_domain(row)
                total, done = self._count_progress(p.outline)
                items.append(ProjectListItem(
                    id=p.id, name=p.name, client_name=p.client_name, description=p.description,
                    completion_rate=round(done / total * 100, 1) if total else 0.0,
                    section_count=total, completed_sections=done, stage=p.stage,
                    created_at=p.created_at, updated_at=p.updated_at,
                ))
        return items

    @staticmethod
    def _count_progress(nodes: List[OutlineNode]):
        total, done = 0, 0

        def walk(ns):
            nonlocal total, done
            for n in ns:
                total += 1
                if n.status in ("completed", "reviewed"):
                    done += 1
                walk(n.children)

        walk(nodes)
        return total, done

    def create(self, req: ProjectCreate) -> Project:
        proj_id = f"proj_{uuid.uuid4().hex[:10]}"
        project = Project(
            id=proj_id,
            name=req.name,
            client_name=req.client_name or "",
            description=req.description or "",
            facts=req.facts or GlobalFacts(),
            outline=[],
            stage="created",
            created_at=now_str(),
            updated_at=now_str(),
        )
        with get_session() as session:
            session.add(ProjectModel(
                id=proj_id, name=project.name, client_name=project.client_name,
                description=project.description, facts_json=project.facts.model_dump_json(),
                outline_json="[]", deviation_json="[]", stage="created", status="active",
                created_at=project.created_at, updated_at=project.updated_at,
            ))
        return project

    def save(self, project: Project):
        """整项目写回（大纲/事实/偏离等变更后调用）"""
        with get_session() as session:
            row = session.get(ProjectModel, project.id)
            if not row:
                raise KeyError(f"项目不存在: {project.id}")
            self._apply_domain(row, project)
            session.add(row)

    def update_stage(self, project_id: str, stage: str):
        with get_session() as session:
            row = session.get(ProjectModel, project_id)
            if row:
                row.stage = stage
                row.updated_at = now_str()
                session.add(row)

    def delete(self, project_id: str) -> bool:
        with get_session() as session:
            row = session.get(ProjectModel, project_id)
            if not row:
                return False
            session.delete(row)
        return True


project_store = ProjectStore()
