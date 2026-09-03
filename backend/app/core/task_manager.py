"""
后台任务管理器：长耗时操作（知识库入库、批量生成、合规审查、拆标分析）
统一走后台线程 + 任务记录，前端通过轮询 /api/v1/tasks/{id} 获取进度。

设计要点：
- ThreadPoolExecutor 限制并发（LLM 调用天然串行友好）
- 任务记录内存态（单用户单进程场景足够），重启后历史任务丢弃
- progress 0-100 + message 中文进度描述 + result/error 载荷
"""
import logging
import threading
import uuid
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("easywrite.tasks")


@dataclass
class TaskRecord:
    id: str
    type: str
    status: str = "pending"  # pending / running / completed / failed / cancelled
    progress: int = 0
    message: str = ""
    result: Any = None
    error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    updated_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class TaskContext:
    """传入任务函数的进度上报句柄"""

    def __init__(self, record: TaskRecord, manager: "TaskManager"):
        self._record = record
        self._manager = manager

    def report(self, progress: int, message: str = ""):
        self._record.progress = max(0, min(100, int(progress)))
        if message:
            self._record.message = message
        self._record.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class TaskManager:
    def __init__(self, max_workers: int = 4):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ew-task")
        self._tasks: Dict[str, TaskRecord] = {}
        self._lock = threading.Lock()
        # 取消标记：任务函数应在合适时机检查 ctx.cancelled()
        self._cancel_flags: Dict[str, threading.Event] = {}

    def submit(self, task_type: str, fn: Callable[[TaskContext], Any], description: str = "") -> str:
        """提交后台任务，立即返回 task_id。fn 接收 TaskContext，返回值作为 result。"""
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        record = TaskRecord(id=task_id, type=task_type, message=description or "任务已提交")
        cancel_event = threading.Event()
        with self._lock:
            self._tasks[task_id] = record
            self._cancel_flags[task_id] = cancel_event

        ctx = TaskContext(record, self)
        ctx.cancelled = cancel_event.is_set  # type: ignore[attr-defined]

        def _run():
            record.status = "running"
            record.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
                record.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with self._lock:
                    self._cancel_flags.pop(task_id, None)

        self._executor.submit(_run)
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
        return record.to_dict() if record else None

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            records = sorted(self._tasks.values(), key=lambda r: r.created_at, reverse=True)
        return [r.to_dict() for r in records[:limit]]


task_manager = TaskManager()
