"""
后台任务管理器：长耗时操作（知识库入库、批量生成、合规审查、拆标分析）
统一走后台线程 + 任务记录，前端通过轮询 /api/v1/tasks/{id} 获取进度。

设计要点：
- ThreadPoolExecutor 限制并发（LLM 调用天然串行友好）
- 运行中的任务以内存记录为准（轮询不查库）；提交、开始、结束时写入 tasks 表，进度节流落库
- 服务重启后库里仍为 pending/running 的任务标记为 interrupted（"已中断"），历史任务与结果可查
- 任务函数在提交时的上下文副本中运行：所属项目等请求上下文随任务进入工作线程；current_task_id 设为任务 ID
  （模型调用记录的 run_id）
- meta 为任务附加信息（如所属章节），页面据此重新挂接进行中的任务
- progress 0-100 + message 中文进度描述 + result/error 载荷
"""
import contextvars
import json
import logging
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from sqlmodel import col, delete, select

from app.core.request_context import current_project_id, current_task_id
from app.db.database import get_session
from app.db.models import TaskModel

logger = logging.getLogger("easywrite.tasks")

ACTIVE_STATUSES = ("pending", "running")
INTERRUPTED_MESSAGE = "服务在任务完成前重启，任务已中断（中断前已保存的结果仍保留），请重新发起"
# 进度落库的最小间隔（秒）：仅重启后查看历史用，轮询读内存
PERSIST_INTERVAL = 2.0
# 库中保留的已结束任务数
KEEP_FINISHED = 200


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _dump_result(result: Any) -> str:
    if result is None:
        return ""
    try:
        return json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps(result, ensure_ascii=False, default=str)


@dataclass
class TaskRecord:
    id: str
    type: str
    title: str = ""
    project_id: str = ""
    status: str = "pending"  # pending / running / completed / failed / cancelled / interrupted
    progress: int = 0
    message: str = ""
    result: Any = None
    error: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    created_ts: float = field(default_factory=time.time)
    last_persist: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "project_id": self.project_id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "meta": self.meta,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_row(self) -> TaskModel:
        return TaskModel(
            id=self.id, type=self.type, title=self.title, project_id=self.project_id,
            status=self.status, progress=self.progress, message=self.message,
            result_json=_dump_result(self.result), error=self.error,
            meta_json=_dump_result(self.meta) if self.meta else "",
            created_at=self.created_at, updated_at=self.updated_at, created_ts=self.created_ts,
        )


def _loads(raw: str) -> Any:
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


def _row_to_dict(row: TaskModel, with_result: bool = True) -> Dict[str, Any]:
    result = _loads(row.result_json) if with_result else None
    return {
        "id": row.id,
        "type": row.type,
        "title": row.title,
        "project_id": row.project_id,
        "status": row.status,
        "progress": row.progress,
        "message": row.message,
        "result": result,
        "error": row.error,
        "meta": _loads(row.meta_json) or {},
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class TaskContext:
    """传入任务函数的进度上报句柄"""

    def __init__(self, record: TaskRecord, manager: "TaskManager"):
        self._record = record
        self._manager = manager

    @property
    def task_id(self) -> str:
        return self._record.id

    def report(self, progress: int, message: str = ""):
        self._record.progress = max(0, min(100, int(progress)))
        if message:
            self._record.message = message
        self._record.updated_at = _now()
        self._manager._persist(self._record)


class TaskManager:
    def __init__(self, max_workers: int = 4):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ew-task")
        # 本进程提交、尚未结束的任务；结束后以库中记录为准
        self._tasks: Dict[str, TaskRecord] = {}
        self._lock = threading.Lock()
        # 取消标记：任务函数应在合适时机检查 ctx.cancelled()
        self._cancel_flags: Dict[str, threading.Event] = {}

    def _persist(self, record: TaskRecord, force: bool = False):
        now = time.monotonic()
        if not force and now - record.last_persist < PERSIST_INTERVAL:
            return
        record.last_persist = now
        try:
            with get_session() as session:
                session.merge(record.to_row())
        except Exception:
            # 落库失败不影响任务本身（轮询仍读内存），只是重启后看不到该记录
            logger.exception("任务记录落库失败 %s", record.id)

    def submit(
        self,
        task_type: str,
        fn: Callable[[TaskContext], Any],
        description: str = "",
        project_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        提交后台任务，立即返回 task_id。fn 接收 TaskContext，返回值作为 result。
        project_id 缺省时取当前请求所属项目（URL 中的 /project/{id}）；meta 为附加信息（如 {"section_id": …}）。
        """
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        record = TaskRecord(
            id=task_id, type=task_type, title=description,
            project_id=project_id if project_id is not None else current_project_id.get(),
            message=description or "任务已提交", meta=dict(meta or {}),
        )
        cancel_event = threading.Event()
        with self._lock:
            self._tasks[task_id] = record
            self._cancel_flags[task_id] = cancel_event
        self._persist(record, force=True)

        ctx = TaskContext(record, self)
        ctx.cancelled = cancel_event.is_set  # type: ignore[attr-defined]

        def _run():
            if record.project_id:
                current_project_id.set(record.project_id)
            current_task_id.set(task_id)
            record.status = "running"
            record.updated_at = _now()
            self._persist(record, force=True)
            try:
                result = fn(ctx)
                if cancel_event.is_set():
                    record.status = "cancelled"
                else:
                    record.status = "completed"
                    record.progress = 100
                record.result = result
            except Exception as e:
                logger.exception("后台任务 %s(%s) 失败", task_type, task_id)
                record.status = "failed"
                record.error = f"{e}\n{traceback.format_exc(limit=3)}"
            finally:
                record.updated_at = _now()
                self._persist(record, force=True)
                with self._lock:
                    self._cancel_flags.pop(task_id, None)
                    self._tasks.pop(task_id, None)

        self._executor.submit(contextvars.copy_context().run, _run)
        return task_id

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            event = self._cancel_flags.get(task_id)
            record = self._tasks.get(task_id)
        if event and record and record.status == "running":
            event.set()
            return True
        return False

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            record = self._tasks.get(task_id)
        if record:
            return record.to_dict()
        with get_session() as session:
            row = session.get(TaskModel, task_id)
            return _row_to_dict(row) if row else None

    def list(
        self,
        limit: int = 50,
        project_id: Optional[str] = None,
        task_type: Optional[str] = None,
        active_only: bool = False,
        with_result: bool = False,
    ) -> List[Dict[str, Any]]:
        """最近任务（新→旧）。运行中的任务用内存中的最新进度覆盖库中的节流快照。"""
        stmt = select(TaskModel)
        if project_id is not None:
            stmt = stmt.where(TaskModel.project_id == project_id)
        if task_type:
            stmt = stmt.where(TaskModel.type == task_type)
        if active_only:
            stmt = stmt.where(col(TaskModel.status).in_(ACTIVE_STATUSES))
        stmt = stmt.order_by(col(TaskModel.created_ts).desc()).limit(limit)
        with get_session() as session:
            items = [_row_to_dict(row, with_result) for row in session.exec(stmt).all()]
        with self._lock:
            live = {tid: r.to_dict() for tid, r in self._tasks.items()}
        for i, item in enumerate(items):
            fresh = live.get(item["id"])
            if fresh:
                if not with_result:
                    fresh["result"] = None
                items[i] = fresh
        return items

    def recover_interrupted(self) -> int:
        """
        启动时调用：库中仍为 pending/running、但并非本进程在跑的任务一律标记为 interrupted；
        同时只保留最近 KEEP_FINISHED 条已结束任务。返回标记的任务数。
        """
        with self._lock:
            live_ids = set(self._tasks)
        marked = 0
        with get_session() as session:
            rows = session.exec(select(TaskModel).where(col(TaskModel.status).in_(ACTIVE_STATUSES))).all()
            for row in rows:
                if row.id in live_ids:
                    continue
                row.status = "interrupted"
                row.message = INTERRUPTED_MESSAGE
                row.error = INTERRUPTED_MESSAGE
                row.updated_at = _now()
                session.add(row)
                marked += 1
            session.flush()
            keep = select(TaskModel.id).order_by(col(TaskModel.created_ts).desc()).limit(KEEP_FINISHED)
            session.exec(delete(TaskModel).where(
                col(TaskModel.status).not_in(ACTIVE_STATUSES), col(TaskModel.id).not_in(keep),
            ))
        if marked:
            logger.warning("%d 个后台任务因服务重启被标记为已中断", marked)
        return marked


task_manager = TaskManager()
