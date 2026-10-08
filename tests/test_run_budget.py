"""阶段 D3：运行预算（模型请求次数与时长上限）、调用记录的 run_id 与按运行汇总、任务附加信息"""
import asyncio
import time
import uuid
from types import SimpleNamespace

import pytest
from sqlmodel import select

from app.core import llm_usage
from app.core.llm_client import llm_client
from app.core.request_context import current_project_id, current_task_id
from app.core.run_budget import BudgetExceeded, RunBudget, current_run
from app.core.task_manager import task_manager
from app.db.database import get_session
from app.db.models import LLMCallLog


def _resp(content, finish_reason="stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15, completion_tokens_details=None),
    )


def _fake_sync(monkeypatch, responses):
    seq = iter(responses)
    sent = []

    def create(**kw):
        sent.append(kw)
        return next(seq)

    monkeypatch.setattr(llm_client, "is_configured", True)
    monkeypatch.setattr(llm_client, "model", "fake-model")
    monkeypatch.setattr(llm_client, "max_tokens", 1000)
    monkeypatch.setattr(llm_client, "client", SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    return sent


@pytest.fixture()
def scope():
    """独立的项目归属与运行 ID，只查本用例写入的记录"""
    pid, run_id = f"proj_budget_{uuid.uuid4().hex[:8]}", f"task_{uuid.uuid4().hex[:12]}"
    tokens = (current_project_id.set(pid), current_task_id.set(run_id))
    yield pid, run_id
    current_project_id.reset(tokens[0])
    current_task_id.reset(tokens[1])


def _rows(project_id):
    with get_session() as session:
        return [r.model_dump() for r in session.exec(
            select(LLMCallLog).where(LLMCallLog.project_id == project_id).order_by(LLMCallLog.id)).all()]


def _in_run(budget, fn):
    token = current_run.set(budget)
    try:
        return fn()
    finally:
        current_run.reset(token)


def test_budget_counts_calls_and_time():
    now = [0.0]
    budget = RunBudget("r", max_calls=2, max_seconds=60, clock=lambda: now[0])
    budget.charge("rerank")
    budget.charge("section_revise")
    with pytest.raises(BudgetExceeded) as exc:
        budget.charge("section_revise")
    assert exc.value.reason == "calls" and budget.calls == 2
    assert budget.snapshot()["by_purpose"] == {"rerank": 1, "section_revise": 1}

    budget = RunBudget("r", max_calls=5, max_seconds=60, clock=lambda: now[0])
    now[0] = 61
    with pytest.raises(BudgetExceeded) as exc:
        budget.charge()
    assert exc.value.reason == "time" and budget.snapshot()["exceeded"] == "time"


def test_truncation_retry_hits_budget_and_is_not_mocked(monkeypatch, scope):
    pid, run_id = scope
    sent = _fake_sync(monkeypatch, [_resp("", "length"), _resp("放大额度后的正文")])
    budget = RunBudget(run_id, max_calls=1)
    with pytest.raises(BudgetExceeded):
        _in_run(budget, lambda: llm_client.chat_completion("系统", "用户", purpose="section_revise"))
    # 只发出了第一次请求；被拦下的放大重试不发请求、不写调用记录，也不退回离线演示
    assert len(sent) == 1 and len(_rows(pid)) == 1
    assert _rows(pid)[0]["run_id"] == run_id

    # 额度足够时照常完成，每次请求计数（含放大重试）
    sent.clear()
    _fake_sync(monkeypatch, [_resp("", "length"), _resp("正文")])
    budget = RunBudget(run_id, max_calls=5)
    assert _in_run(budget, lambda: llm_client.chat_completion("系统", "用户", purpose="section_revise")) == "正文"
    assert budget.calls == 2


def test_structured_call_propagates_budget_instead_of_returning_none(monkeypatch, scope):
    _fake_sync(monkeypatch, [])
    budget = RunBudget(scope[1], max_calls=0)
    with pytest.raises(BudgetExceeded):
        _in_run(budget, lambda: llm_client.chat_completion_structured("系统", "用户", purpose="rerank"))


def test_async_stream_and_async_call_are_charged(monkeypatch, scope):
    async def create(**kw):
        raise AssertionError("超出预算后不应发出请求")

    monkeypatch.setattr(llm_client, "is_configured", True)
    monkeypatch.setattr(llm_client, "async_client", SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    budget = RunBudget(scope[1], max_calls=0)

    async def stream():
        return [t async for t in llm_client.chat_completion_stream_async("系统", "用户", purpose="section_write")]

    with pytest.raises(BudgetExceeded):
        _in_run(budget, lambda: asyncio.run(stream()))
    with pytest.raises(BudgetExceeded):
        _in_run(budget, lambda: asyncio.run(llm_client.chat_completion_async("系统", "用户", purpose="x")))
    assert _rows(scope[0]) == []


def test_no_budget_outside_a_run(monkeypatch, scope):
    _fake_sync(monkeypatch, [_resp("正文")] * 20)
    for _ in range(20):
        assert llm_client.chat_completion("系统", "用户", purpose="section_write") == "正文"


def test_usage_by_run_and_run_filter(monkeypatch, scope):
    pid, run_id = scope
    _fake_sync(monkeypatch, [_resp("a"), _resp("b")])
    llm_client.chat_completion("系统", "用户", purpose="section_revise")
    llm_client.chat_completion("系统", "用户", purpose="rerank")
    totals = llm_usage.run_totals(run_id)
    assert (totals["calls"], totals["total_tokens"], totals["calls_without_usage"]) == (2, 30, 0)

    summary = llm_usage.summarize(days=1, project_id=pid)
    assert [(r["run_id"], r["calls"], r["total_tokens"]) for r in summary["by_run"]] == [(run_id, 2, 30)]
    only = llm_usage.summarize(days=1, run_id=run_id)
    assert only["totals"]["calls"] == 2 and only["run_id"] == run_id


def test_task_runs_carry_task_id_and_meta(monkeypatch, client):
    _fake_sync(monkeypatch, [_resp("正文")])
    pid = f"proj_budget_{uuid.uuid4().hex[:8]}"
    task_id = task_manager.submit(
        "section_refine", lambda ctx: llm_client.chat_completion("系统", "用户", purpose="section_revise"),
        description="测试", project_id=pid, meta={"section_id": "sec_9"},
    )
    for _ in range(200):
        task = task_manager.get(task_id)
        if task["status"] not in ("pending", "running"):
            break
        time.sleep(0.02)
    assert task["status"] == "completed" and task["meta"] == {"section_id": "sec_9"}
    assert [r["run_id"] for r in _rows(pid)] == [task_id]
    listed = client.get(f"/api/v1/tasks?project_id={pid}").json()["tasks"]
    assert listed[0]["meta"] == {"section_id": "sec_9"}
    by_run = client.get(f"/api/v1/ai/usage?days=1&project_id={pid}").json()["by_run"]
    assert by_run[0]["run_id"] == task_id and by_run[0]["type"] == "section_refine" and by_run[0]["title"] == "测试"
