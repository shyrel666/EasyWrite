"""
SQLite 持久层（SQLModel）：
- WAL 模式保证读写并发安全，替代原来"全量重写 JSON 文件"的持久化方式
- 嵌套结构（大纲树/事实/偏离矩阵）以 JSON 列存储——访问模式总是整树加载
- init_db() 建表；migrate_legacy_json() 一次性导入旧 projects.json / knowledge_index.json
"""
import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from sqlmodel import SQLModel, Session, create_engine, select

from app.core.config import settings

logger = logging.getLogger("easywrite.db")

DB_PATH = settings.DB_PATH

# check_same_thread=False: FastAPI 多线程（threadpool 端点 + 后台任务）共享引擎
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
    echo=False,
)


@contextmanager
def get_session() -> Iterator[Session]:
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    import app.db.models  # noqa: F401 确保模型注册

    SQLModel.metadata.create_all(engine)


def migrate_legacy_json() -> None:
    """一次性迁移旧 JSON 数据文件（存在且非空时）。幂等：仅在对应表为空时执行。"""
    from app.db.models import ProjectModel, KBDocument, KBChunk

    with get_session() as session:
        has_projects = session.exec(select(ProjectModel).limit(1)).first() is not None

        # 1. 迁移 projects.json
        legacy_projects = settings.DATA_DIR / "projects.json"
        if not has_projects and legacy_projects.exists():
            try:
                data = json.loads(legacy_projects.read_text(encoding="utf-8"))
                count = 0
                for pid, pdata in data.items():
                    if not isinstance(pdata, dict):
                        continue
                    session.add(_project_from_legacy(pid, pdata))
                    count += 1
                if count:
                    logger.info("已从 projects.json 迁移 %d 个项目", count)
            except Exception as e:
                logger.error("迁移 projects.json 失败: %s", e)

        # 2. 迁移 knowledge_index.json（旧 TF-IDF 知识库）
        has_docs = session.exec(select(KBDocument).limit(1)).first() is not None
        legacy_kb = settings.KNOWLEDGE_DIR / "knowledge_index.json"
        if not has_docs and legacy_kb.exists():
            try:
                data = json.loads(legacy_kb.read_text(encoding="utf-8"))
                chunks = data.get("chunks", [])
                by_doc: dict = {}
                for c in chunks:
                    by_doc.setdefault(c.get("doc_name", "未命名文档"), []).append(c)
                for doc_name, doc_chunks in by_doc.items():
                    doc = KBDocument(
                        id=f"kbdoc_{abs(hash(doc_name)) % 10**10}",
                        doc_name=doc_name,
                        status="ready",
                        chunk_count=len(doc_chunks),
                    )
                    session.add(doc)
                    for i, c in enumerate(doc_chunks):
                        session.add(KBChunk(
                            id=c.get("id") or f"chunk_{doc.id}_{i}",
                            doc_id=doc.id,
                            breadcrumb=c.get("breadcrumb", ""),
                            section_title=c.get("section_title", ""),
                            content=c.get("content", ""),
                            context_text=c.get("breadcrumb", "") + "\n" + c.get("content", "")[:200],
                            tags_json=json.dumps(c.get("tags", []), ensure_ascii=False),
                            seq=i,
                        ))
                if by_doc:
                    logger.info("已从 knowledge_index.json 迁移 %d 个文档", len(by_doc))
            except Exception as e:
                logger.error("迁移 knowledge_index.json 失败: %s", e)


def _project_from_legacy(pid: str, pdata: dict) -> "ProjectModel":
    from app.db.models import ProjectModel

    return ProjectModel(
        id=pid,
        name=pdata.get("name", "未命名项目"),
        client_name=pdata.get("client_name", ""),
        description=pdata.get("description", ""),
        facts_json=json.dumps(pdata.get("facts", {}), ensure_ascii=False),
        tender_analysis_json=(
            json.dumps(pdata["tender_analysis"], ensure_ascii=False)
            if pdata.get("tender_analysis")
            else None
        ),
        outline_json=json.dumps(pdata.get("outline", []), ensure_ascii=False),
        deviation_json=json.dumps(pdata.get("deviation_matrix", []), ensure_ascii=False),
        status=pdata.get("status", "active"),
        created_at=pdata.get("created_at", ""),
        updated_at=pdata.get("updated_at", ""),
    )


init_db()
