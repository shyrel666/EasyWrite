"""
模型调用记录：每一次真实的模型请求写一行 llm_calls（耗时、用量、结束原因、所属项目与用途）。

- 截断后放大额度重试、限流重试、失败的请求各记一行，如实反映消耗；离线模拟器不调用模型，不记录
- 用量取服务商返回的 usage；未返回时留空，不做估算
- 记录失败只写日志，绝不影响生成本身
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import case, func
from sqlmodel import col, delete, select

from app.core.request_context import current_project_id
from app.db.database import get_session
from app.db.models import LLMCallLog

logger = logging.getLogger("easywrite.llm.usage")

KEEP_DAYS = 180
ERROR_MAX_CHARS = 300


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _field(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _int_or_none(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def usage_fields(usage: Any) -> Dict[str, Optional[int]]:
    """OpenAI 兼容 usage → 计数字段（推理模型的思考 token 在 completion_tokens_details.reasoning_tokens）"""
    return {
        "prompt_tokens": _int_or_none(_field(usage, "prompt_tokens")),
        "completion_tokens": _int_or_none(_field(usage, "completion_tokens")),
        "total_tokens": _int_or_none(_field(usage, "total_tokens")),
        "reasoning_tokens": _int_or_none(_field(_field(usage, "completion_tokens_details"), "reasoning_tokens")),
    }


def record(
    *,
    purpose: str,
    kind: str,
    model: str,
    started: float,
    max_tokens: int = 0,
    usage: Any = None,
    finish_reason: str = "",
    empty: bool = False,
    error: Optional[BaseException] = None,
    first_token_at: Optional[float] = None,
) -> None:
    """写入一条调用记录。started / first_token_at 为 time.perf_counter() 读数。"""
    if error is not None:
        aborted = isinstance(error, (asyncio.CancelledError, GeneratorExit))
        status = "aborted" if aborted else "error"
    elif finish_reason == "length":
        status = "truncated"
    elif empty:
        status = "empty"
    else:
        status = "ok"
    row = LLMCallLog(
        created_at=_now(),
        project_id=current_project_id.get(),
        purpose=purpose or "other",
        kind=kind,
        model=model or "",
        status=status,
        latency_ms=int((time.perf_counter() - started) * 1000),
        first_token_ms=int((first_token_at - started) * 1000) if first_token_at is not None else None,
        max_tokens=int(max_tokens or 0),
        finish_reason=finish_reason or "",
        error=(f"{type(error).__name__}: {error}"[:ERROR_MAX_CHARS] if status == "error" else ""),
        **usage_fields(usage),
    )
    try:
        with get_session() as session:
            session.add(row)
    except Exception:
        logger.exception("模型调用记录写入失败")


class StreamMeter:
    """流式请求计量：首 token 时间、末尾的 usage 块（stream_options.include_usage）、结束原因"""

    def __init__(self, purpose: str, model: str, max_tokens: int):
        self.purpose = purpose
        self.model = model
        self.max_tokens = max_tokens
        self.started = time.perf_counter()
        self.first_token_at: Optional[float] = None
        self.usage: Any = None
        self.finish_reason = ""
        self._done = False

    def on_chunk(self, chunk: Any) -> str:
        """吸收一个流式块，返回其中的正文增量（可能为空串）"""
        usage = getattr(chunk, "usage", None)
        if usage:
            self.usage = usage
        if not getattr(chunk, "choices", None):
            return ""
        choice = chunk.choices[0]
        self.finish_reason = getattr(choice, "finish_reason", None) or self.finish_reason
        delta = getattr(choice, "delta", None)
        content = getattr(delta, "content", None) or ""
        if content and self.first_token_at is None:
            self.first_token_at = time.perf_counter()
        return content

    def finish(self, error: Optional[BaseException] = None) -> None:
        if self._done:
            return
        self._done = True
        record(
            purpose=self.purpose, kind="stream", model=self.model, started=self.started,
            max_tokens=self.max_tokens, usage=self.usage, finish_reason=self.finish_reason,
            empty=self.first_token_at is None, error=error, first_token_at=self.first_token_at,
        )


# ---------------- 统计查询 ----------------

def _filters(since: str, project_id: Optional[str]):
    conds = [col(LLMCallLog.created_at) >= since]
    if project_id is not None:
        conds.append(LLMCallLog.project_id == project_id)
    return conds


def _group_rows(session, key, conds) -> List[Dict[str, Any]]:
    stmt = (
        select(
            key,
            func.count(),
            func.sum(case((LLMCallLog.status == "error", 1), else_=0)),
            func.sum(LLMCallLog.total_tokens),
            func.avg(LLMCallLog.latency_ms),
        )
        .where(*conds)
        .group_by(key)
        .order_by(func.count().desc())
    )
    return [
        {"key": k, "calls": n, "errors": int(err or 0), "total_tokens": int(tok or 0), "avg_latency_ms": int(avg or 0)}
        for k, n, err, tok, avg in session.exec(stmt).all()
    ]


def summarize(days: int = 7, project_id: Optional[str] = None, recent_limit: int = 30) -> Dict[str, Any]:
    """最近 days 天的调用汇总：总量、按状态/用途/模型/日期分组、p95 耗时与最近调用明细"""
    since_day = (datetime.now() - timedelta(days=max(1, days) - 1)).strftime("%Y-%m-%d")
    since = f"{since_day} 00:00:00"
    conds = _filters(since, project_id)
    with get_session() as session:
        calls, prompt, completion, reasoning, total, avg_latency, no_usage = session.exec(
            select(
                func.count(),
                func.sum(LLMCallLog.prompt_tokens),
                func.sum(LLMCallLog.completion_tokens),
                func.sum(LLMCallLog.reasoning_tokens),
                func.sum(LLMCallLog.total_tokens),
                func.avg(LLMCallLog.latency_ms),
                func.sum(case((col(LLMCallLog.total_tokens).is_(None), 1), else_=0)),
            ).where(*conds)
        ).one()
        by_status = {s: n for s, n in session.exec(
            select(LLMCallLog.status, func.count()).where(*conds).group_by(LLMCallLog.status)
        ).all()}
        p95 = 0
        if calls:
            p95 = session.exec(
                select(LLMCallLog.latency_ms).where(*conds)
                .order_by(col(LLMCallLog.latency_ms)).offset(int(0.95 * (calls - 1))).limit(1)
            ).first() or 0
        day = func.substr(LLMCallLog.created_at, 1, 10)
        by_day = [
            {"day": d, "calls": n, "total_tokens": int(tok or 0)}
            for d, n, tok in session.exec(
                select(day, func.count(), func.sum(LLMCallLog.total_tokens)).where(*conds).group_by(day).order_by(day)
            ).all()
        ]
        by_purpose = [{"purpose": r.pop("key"), **r} for r in _group_rows(session, LLMCallLog.purpose, conds)]
        by_model = [{"model": r.pop("key"), **r} for r in _group_rows(session, LLMCallLog.model, conds)]
        recent = [
            row.model_dump() for row in session.exec(
                select(LLMCallLog).where(*conds).order_by(col(LLMCallLog.id).desc()).limit(recent_limit)
            ).all()
        ]
    return {
        "days": days,
        "since": since,
        "project_id": project_id,
        "totals": {
            "calls": calls,
            "errors": by_status.get("error", 0),
            "prompt_tokens": int(prompt or 0),
            "completion_tokens": int(completion or 0),
            "reasoning_tokens": int(reasoning or 0),
            "total_tokens": int(total or 0),
            "calls_without_usage": int(no_usage or 0),
            "avg_latency_ms": int(avg_latency or 0),
            "p95_latency_ms": int(p95),
        },
        "by_status": by_status,
        "by_purpose": by_purpose,
        "by_model": by_model,
        "by_day": by_day,
        "recent": recent,
    }


def prune(keep_days: int = KEEP_DAYS) -> int:
    """删除 keep_days 天以前的记录（启动时调用）"""
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S")
    with get_session() as session:
        result = session.exec(delete(LLMCallLog).where(col(LLMCallLog.created_at) < cutoff))
        return result.rowcount or 0
