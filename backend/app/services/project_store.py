"""
项目仓储层：SQLite 持久化与 Pydantic 领域模型之间的转换。

替代原 endpoints.py 中的内存 dict + 全量重写 JSON 文件方案：
- 每次变更单行 UPDATE，WAL 模式并发安全
- 大纲树/事实/偏离矩阵序列化为 JSON 列（访问模式总是整树加载）
- 写入统一走 update()：写锁内重新读取最新行再修改，杜绝"旧快照整体写回"
  覆盖并发编辑（如流式生成数十秒后写回，冲掉期间其他章节的自动保存）
"""
import json
import logging
import threading
import uuid
from datetime import datetime
from typing import Callable, List, Optional, Tuple, TypeVar

from sqlmodel import Session, select

from app.db.database import get_session
from app.db.models import ProjectModel
from app.models.schemas import (
    Project, ProjectCreate, ProjectListItem, GlobalFacts,
    TenderAnalysis18, DeviationItem, OutlineNode,
)

logger = logging.getLogger("easywrite.project_store")

# 招标正文存储上限（防超大文件拖垮单行读写）
TENDER_TEXT_LIMIT = 200_000

# 单进程内串行化项目的"读-改-写"（本系统为单用户单进程部署）
_write_lock = threading.RLock()

T = TypeVar("T")


class ProjectNotFound(KeyError):
    """项目不存在（main.py 统一映射为 404）"""


def find_node(nodes: List[OutlineNode], section_id: str) -> Optional[OutlineNode]:
    """在大纲树中按 id 深度优先查找节点"""
    for n in nodes:
        if n.id == section_id:
            return n
        found = find_node(n.children, section_id)
        if found:
            return found
    return None


# 只经章节接口修改的字段：大纲整树保存时，已存在节点保留服务端的值
SERVER_OWNED_FIELDS = ("content", "status", "last_refs", "revision", "content_source", "pinned_refs", "excluded_refs")


def merge_outline(server: List[OutlineNode], client: List[OutlineNode]) -> Tuple[List[OutlineNode], List[str]]:
    """
    大纲整树保存的合并规则，返回 (合并后的大纲, 被删除的章节 ID)：
    结构、标题、要求、字数预算等取客户端提交的值；已存在节点的正文、状态、引用与修订号保留服务端的值
    （正文只能经章节接口修改，客户端的旧快照不会覆盖后台写入的正文）；新节点取客户端的值，修订号从 0 开始。
    """
    existing = {n.id: n for n in _walk(server)}
    seen = set()

    def merge(node: OutlineNode) -> OutlineNode:
        seen.add(node.id)
        merged = node.model_copy(update={"children": [merge(c) for c in node.children]})
        current = existing.get(node.id)
        if current is not None:
            for field in SERVER_OWNED_FIELDS:
                setattr(merged, field, getattr(current, field))
        else:
            merged.revision = 0
            merged.content_source = "manual" if merged.content.strip() else ""
        return merged

    merged = [merge(n) for n in client]
    return merged, [sid for sid in existing if sid not in seen]


def _walk(nodes: List[OutlineNode]) -> List[OutlineNode]:
    out: List[OutlineNode] = []
    for n in nodes:
        out.append(n)
        out.extend(_walk(n.children))
    return out


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
            evidence_links=json.loads(row.evidence_json or "{}"),
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
        row.evidence_json = json.dumps(project.evidence_links, ensure_ascii=False)
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

    def get_tender_document(self, project_id: str) -> dict:
        """一次读取同一份招标正文、章节树与来源，避免更新期间混合两份文件。"""
        with get_session() as session:
            row = session.get(ProjectModel, project_id)
            if not row:
                return {"text": "", "structure": None, "source": ""}
            return {"text": row.tender_text,
                    "structure": json.loads(row.tender_structure_json) if row.tender_structure_json else None,
                    "source": row.tender_source}

    def set_tender_document(self, project_id: str, text: str, parsed: Optional[dict] = None):
        """上传文件携带 parsed；纯文本（parsed 为 None）同时清除此前上传文件的章节树。所有来源字段在一个事务内更新。"""
        with _write_lock, get_session() as session:
            self.set_tender_document_in(session, project_id, text, parsed)

    @staticmethod
    def set_tender_document_in(session: Session, project_id: str, text: str, parsed: Optional[dict] = None):
        """供 update 的 also 钩子使用：应用拆标结论与替换原文也必须同事务提交。"""
        row = session.get(ProjectModel, project_id)
        if row is None:
            raise ProjectNotFound(project_id)
        row.tender_text = (text or "")[:TENDER_TEXT_LIMIT]
        if parsed is None:
            row.tender_structure_json, row.tender_source = "", "text"
        else:
            payload = {"heading_mode": parsed.get("heading_mode", ""), "sections": parsed.get("sections", [])}
            row.tender_structure_json = json.dumps(payload, ensure_ascii=False)
            row.tender_source = parsed.get("source_format") or "document"
        row.updated_at = now_str()
        session.add(row)

    def get_tender_structure(self, project_id: str) -> Optional[dict]:
        return self.get_tender_document(project_id)["structure"]

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

    def update(
        self, project_id: str, mutate: Callable[[Project], T],
        also: Optional[Callable[[Session, T], None]] = None,
    ) -> T:
        """
        原子更新：写锁 + 单事务内读取最新项目 → mutate 就地修改 → 写回，返回 mutate 的返回值。
        mutate 内只做内存修改；LLM 调用等耗时操作必须放在 update 之外，避免长时间占锁。
        also(session, result)：在同一事务内、提交前执行的附加写入（如章节历史版本）。
        mutate 或 also 抛出异常时整个事务回滚、项目与附加写入都不落库。
        """
        with _write_lock, get_session() as session:
            row = session.get(ProjectModel, project_id)
            if not row:
                raise ProjectNotFound(project_id)
            project = self._to_domain(row)
            result = mutate(project)
            self._apply_domain(row, project)
            session.add(row)
            if also is not None:
                also(session, result)
        return result

    def save(self, project: Project):
        """整项目覆盖写回。仅适用于刚读出、未经耗时操作的对象；API 层请使用 update()"""
        with _write_lock, get_session() as session:
            row = session.get(ProjectModel, project.id)
            if not row:
                raise ProjectNotFound(project.id)
            self._apply_domain(row, project)
            session.add(row)

    def update_stage(self, project_id: str, stage: str):
        with _write_lock, get_session() as session:
            row = session.get(ProjectModel, project_id)
            if row:
                row.stage = stage
                row.updated_at = now_str()
                session.add(row)

    def delete(self, project_id: str) -> bool:
        with _write_lock, get_session() as session:
            row = session.get(ProjectModel, project_id)
            if not row:
                return False
            session.delete(row)
        return True


project_store = ProjectStore()
