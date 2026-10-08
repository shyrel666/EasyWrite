"""
运行预算：一次运行（如智能完善 section_refine 任务）的模型请求次数与时长上限。

- 上下文变量 current_run 携带本次运行的 RunBudget；不在运行中（为空）时不做任何限制
- llm_client 每次发起真实请求前调用 charge()：检索重排、正文生成、截断后放大额度重试、限流重试都各算一次
- 超出次数或时长时抛 BudgetExceeded。llm_client 不把它当作普通失败（不退回离线演示、不写调用记录），
  一路抛给运行方，由运行方以"部分完成 / 预算用尽"结束，已产生的候选稿保留
"""
import contextvars
import threading
import time
from typing import Callable, Dict, Optional


class BudgetExceeded(Exception):
    """本次运行的模型请求次数或时长已达上限"""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason  # calls / time
        self.message = message


class RunBudget:
    def __init__(self, run_id: str = "", max_calls: int = 15, max_seconds: float = 600,
                 clock: Callable[[], float] = time.monotonic):
        self.run_id = run_id
        self.max_calls = max(0, int(max_calls))
        self.max_seconds = max(0.0, float(max_seconds))
        self._clock = clock
        self.started = clock()
        self.calls = 0
        self.by_purpose: Dict[str, int] = {}
        self.exceeded: Optional[str] = None
        self._lock = threading.Lock()

    def elapsed(self) -> float:
        return self._clock() - self.started

    def charge(self, purpose: str = "") -> None:
        """发起一次模型请求前调用：未超限则计数，超限抛 BudgetExceeded"""
        with self._lock:
            if self.calls >= self.max_calls:
                self.exceeded = "calls"
                raise BudgetExceeded("calls", f"模型请求次数已达本次运行上限（{self.max_calls} 次）")
            if self.elapsed() >= self.max_seconds:
                self.exceeded = "time"
                raise BudgetExceeded("time", f"运行时间已达上限（{self.max_seconds / 60:g} 分钟）")
            self.calls += 1
            key = purpose or "other"
            self.by_purpose[key] = self.by_purpose.get(key, 0) + 1

    def snapshot(self) -> Dict:
        return {"max_calls": self.max_calls, "max_seconds": int(self.max_seconds), "calls": self.calls,
                "elapsed_s": round(self.elapsed(), 1), "by_purpose": dict(self.by_purpose), "exceeded": self.exceeded}


current_run: contextvars.ContextVar[Optional[RunBudget]] = contextvars.ContextVar("current_run", default=None)


def charge(purpose: str = "") -> None:
    """当前上下文处于某次运行中时计入其预算（llm_client 每次真实请求前调用）"""
    budget = current_run.get()
    if budget is not None:
        budget.charge(purpose)
