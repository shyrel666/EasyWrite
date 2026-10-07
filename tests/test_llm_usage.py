"""模型调用记录：每次真实请求一行（耗时/用量/用途/项目），离线模拟不记录，统计接口汇总。"""
import asyncio
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError
from sqlmodel import select

from app.core import llm_usage
from app.core.embedding_client import embedding_client
from app.core.llm_client import llm_client
from app.core.request_context import current_project_id
from app.db.database import get_session
from app.db.models import LLMCallLog

OUTLINE = [{"id": "sec_1", "title": "第一章 总体方案", "level": 1}]


@contextmanager
def _project_scope():
    """给本用例一个独立的项目归属，便于只查自己写入的记录"""
    pid = f"proj_usage_{uuid.uuid4().hex[:8]}"
    token = current_project_id.set(pid)
    try:
        yield pid
    finally:
        current_project_id.reset(token)


def _rows(project_id):
    with get_session() as session:
        rows = session.exec(
            select(LLMCallLog).where(LLMCallLog.project_id == project_id).order_by(LLMCallLog.id)
        ).all()
        return [SimpleNamespace(**row.model_dump()) for row in rows]


def _usage(prompt, completion, reasoning=None):
    details = SimpleNamespace(reasoning_tokens=reasoning) if reasoning is not None else None
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion,
                           total_tokens=prompt + completion, completion_tokens_details=details)


def _resp(content, finish_reason="stop", usage=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(content=content))],
        usage=usage,
    )


def _bad_request(msg="bad request"):
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    return BadRequestError(msg, response=httpx.Response(400, request=request), body=None)


def _fake_sync(monkeypatch, responses):
    seq = iter(responses)

    def create(**kw):
        result = next(seq)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(llm_client, "is_configured", True)
    monkeypatch.setattr(llm_client, "model", "fake-model")
    monkeypatch.setattr(llm_client, "max_tokens", 1000)
    monkeypatch.setattr(llm_client, "client", SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))


class _Stream:
    def __init__(self, chunks, fail_after=None):
        self._chunks = list(chunks)
        self._fail_after = fail_after

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._fail_after is not None and self._fail_after == 0:
            raise ConnectionResetError("connection reset")
        if not self._chunks:
            raise StopAsyncIteration
        if self._fail_after is not None:
            self._fail_after -= 1
        return self._chunks.pop(0)


def _chunk(content=None, finish_reason=None):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish_reason, delta=SimpleNamespace(content=content))],
                           usage=None)


def _usage_chunk(prompt, completion):
    return SimpleNamespace(choices=[], usage=_usage(prompt, completion))


def _fake_async(monkeypatch, create):
    monkeypatch.setattr(llm_client, "is_configured", True)
    monkeypatch.setattr(llm_client, "model", "fake-model")
    monkeypatch.setattr(llm_client, "max_tokens", 1000)
    monkeypatch.setattr(llm_client, "_stream_usage", True)
    monkeypatch.setattr(llm_client, "async_client", SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))


def _consume(gen_factory, stop_after=None):
    async def run():
        out = []
        agen = gen_factory()
        try:
            async for token in agen:
                out.append(token)
                if stop_after is not None and len(out) >= stop_after:
                    break
        finally:
            await agen.aclose()
        return out
    return asyncio.run(run())


def test_sync_call_records_latency_usage_and_purpose(monkeypatch):
    _fake_sync(monkeypatch, [_resp("正文", usage=_usage(120, 30, reasoning=10))])
    with _project_scope() as pid:
        assert llm_client.chat_completion("sys", "user", purpose="polish") == "正文"
    [row] = _rows(pid)
    assert (row.purpose, row.kind, row.model, row.status) == ("polish", "chat", "fake-model", "ok")
    assert (row.prompt_tokens, row.completion_tokens, row.reasoning_tokens, row.total_tokens) == (120, 30, 10, 150)
    assert row.max_tokens == 1000 and row.finish_reason == "stop" and row.latency_ms >= 0


def test_every_request_of_an_escalation_is_recorded(monkeypatch):
    _fake_sync(monkeypatch, [
        _resp("", "length", usage=_usage(100, 1000)),
        _bad_request("max_tokens too large"),
        _resp('{"a": 1}', usage=_usage(100, 300)),
    ])
    with _project_scope() as pid:
        assert llm_client.chat_completion_structured("sys", "user", purpose="tender_extract") == {"a": 1}
    rows = _rows(pid)
    assert [r.status for r in rows] == ["truncated", "error", "ok"]
    assert [r.max_tokens for r in rows] == [1000, 4000, 2000]
    assert "max_tokens too large" in rows[1].error and rows[1].total_tokens is None
    assert {r.purpose for r in rows} == {"tender_extract"}


def test_offline_mock_is_not_recorded():
    assert not llm_client.is_configured
    with _project_scope() as pid:
        llm_client.chat_completion("sys", "user", purpose="polish")
        assert llm_client.chat_completion_structured("sys", "user", purpose="rerank") is None
        _consume(lambda: llm_client.chat_completion_stream_async("sys", "user", purpose="section_write"))
    assert _rows(pid) == []


def test_missing_usage_is_left_empty_not_estimated(monkeypatch):
    _fake_sync(monkeypatch, [_resp("正文")])
    with _project_scope() as pid:
        llm_client.chat_completion("sys", "user", purpose="polish")
    [row] = _rows(pid)
    assert row.status == "ok" and row.total_tokens is None and row.prompt_tokens is None


def test_stream_records_usage_chunk_and_first_token(monkeypatch):
    seen = []

    async def create(**kw):
        seen.append(kw.get("stream_options"))
        return _Stream([_chunk("第一段"), _chunk("第二段", "stop"), _usage_chunk(500, 80)])

    _fake_async(monkeypatch, create)
    with _project_scope() as pid:
        tokens = _consume(lambda: llm_client.chat_completion_stream_async("sys", "user", purpose="section_write"))
    assert tokens == ["第一段", "第二段"]
    assert seen == [{"include_usage": True}]
    [row] = _rows(pid)
    assert (row.kind, row.status, row.purpose) == ("stream", "ok", "section_write")
    assert row.total_tokens == 580 and row.first_token_ms is not None and row.first_token_ms <= row.latency_ms


def test_stream_falls_back_when_provider_rejects_stream_options(monkeypatch):
    seen = []

    async def create(**kw):
        seen.append("stream_options" in kw)
        if "stream_options" in kw:
            raise _bad_request("unknown field stream_options")
        return _Stream([_chunk("正文", "stop")])

    _fake_async(monkeypatch, create)
    with _project_scope() as pid:
        assert _consume(lambda: llm_client.chat_completion_stream_async("sys", "user", purpose="section_write")) == ["正文"]
        assert _consume(lambda: llm_client.chat_completion_stream_async("sys", "user", purpose="section_write")) == ["正文"]
    assert seen == [True, False, False], "拒绝一次后本配置下不再携带 stream_options"
    assert [r.status for r in _rows(pid)] == ["error", "ok", "ok"]


def test_stream_cancelled_by_client_is_recorded_as_aborted(monkeypatch):
    async def create(**kw):
        return _Stream([_chunk("一"), _chunk("二"), _chunk("三", "stop")])

    _fake_async(monkeypatch, create)
    with _project_scope() as pid:
        assert _consume(lambda: llm_client.chat_completion_stream_async("sys", "user", purpose="section_write"),
                        stop_after=1) == ["一"]
    [row] = _rows(pid)
    assert row.status == "aborted" and row.error == ""


def test_stream_broken_midway_is_recorded_as_error(monkeypatch):
    async def create(**kw):
        return _Stream([_chunk("真实分片"), _chunk("不会到达")], fail_after=1)

    _fake_async(monkeypatch, create)
    with _project_scope() as pid:
        with pytest.raises(RuntimeError, match="中断"):
            _consume(lambda: llm_client.chat_completion_stream_async("sys", "user", purpose="section_write"))
    [row] = _rows(pid)
    assert row.status == "error" and "connection reset" in row.error


def test_embedding_requests_are_recorded(monkeypatch):
    def create(input, model):
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2]) for _ in input],
                               usage=SimpleNamespace(prompt_tokens=7, total_tokens=7))

    monkeypatch.setattr(embedding_client, "is_available", True)
    monkeypatch.setattr(embedding_client, "model", "fake-embed")
    monkeypatch.setattr(embedding_client, "client", SimpleNamespace(embeddings=SimpleNamespace(create=create)))
    with _project_scope() as pid:
        assert embedding_client.embed_query_sync("查询").shape == (2,)
    [row] = _rows(pid)
    assert (row.kind, row.purpose, row.model, row.prompt_tokens, row.total_tokens) == ("embedding", "embedding", "fake-embed", 7, 7)


def test_section_stream_endpoint_attributes_calls_to_project(client, project_id, monkeypatch):
    assert client.put(f"/api/v1/project/{project_id}/outline", json={"outline": OUTLINE}).status_code == 200

    async def create(**kw):
        return _Stream([_chunk("方案正文", "stop"), _usage_chunk(900, 60)])

    _fake_async(monkeypatch, create)
    res = client.post(f"/api/v1/project/{project_id}/section/generate/stream", json={
        "project_id": project_id, "section_id": "sec_1", "section_title": "第一章 总体方案",
    })
    assert res.status_code == 200 and '"done": true' in res.text
    rows = [r for r in _rows(project_id) if r.purpose == "section_write"]
    assert len(rows) == 1 and rows[0].total_tokens == 960

    summary = client.get("/api/v1/ai/usage", params={"project_id": project_id, "days": 1}).json()
    assert summary["totals"]["calls"] >= 1 and summary["totals"]["total_tokens"] >= 960
    assert any(p["purpose"] == "section_write" for p in summary["by_purpose"])
    assert summary["recent"][0]["project_id"] == project_id


def test_usage_summary_aggregates(client, monkeypatch):
    with _project_scope() as pid:
        _fake_sync(monkeypatch, [
            _resp("甲", usage=_usage(100, 50)),
            _resp("乙", usage=_usage(200, 50)),
            _bad_request("boom"),
        ])
        llm_client.chat_completion("sys", "user", purpose="polish")
        llm_client.chat_completion("sys", "user", purpose="rerank")
        llm_client.chat_completion("sys", "user", purpose="rerank")  # 失败后退回离线模拟（只记录那次失败）
    data = llm_usage.summarize(days=1, project_id=pid)
    totals = data["totals"]
    assert totals["calls"] == 3 and totals["errors"] == 1
    assert totals["prompt_tokens"] == 300 and totals["total_tokens"] == 400
    assert totals["calls_without_usage"] == 1
    by_purpose = {p["purpose"]: p for p in data["by_purpose"]}
    assert by_purpose["rerank"]["calls"] == 2 and by_purpose["rerank"]["errors"] == 1
    assert by_purpose["polish"]["total_tokens"] == 150
    assert data["by_status"] == {"ok": 2, "error": 1}
    assert len(data["by_day"]) == 1 and data["by_day"][0]["calls"] == 3
    assert [r["purpose"] for r in data["recent"]] == ["rerank", "rerank", "polish"]
    # 不带项目筛选时包含全部记录
    assert client.get("/api/v1/ai/usage").json()["totals"]["calls"] >= 3


def test_prune_removes_old_rows():
    with get_session() as session:
        session.add(LLMCallLog(created_at="2000-01-01 00:00:00", project_id="proj_prune", purpose="polish"))
    assert llm_usage.prune() >= 1
    assert _rows("proj_prune") == []
