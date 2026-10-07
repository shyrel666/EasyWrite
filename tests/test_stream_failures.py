"""
流式生成失败路径：中途失败不得写入不完整正文，也不得拼接模拟文本冒充完整结果。
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.core.llm_client import llm_client
from app.services.generator.section_generator import section_generator
from app.services.project_store import project_store, find_node

OUTLINE = [{"id": "sec_1", "title": "第一章 总体方案", "level": 1, "content": "原有正文", "status": "reviewed"}]


def _sse_events(text: str):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_stream_failure_keeps_original_content(client, project_id, monkeypatch):
    assert client.put(f"/api/v1/project/{project_id}/outline", json={"outline": OUTLINE}).status_code == 200

    async def failing_stream(**kwargs):
        yield {"refs": [], "retrieval_message": "", "mode": "llm"}
        yield {"token": "半截"}
        raise RuntimeError("模型流式输出中断：连接被重置")

    monkeypatch.setattr(section_generator, "draft_section_stream", failing_stream)
    res = client.post(f"/api/v1/project/{project_id}/section/generate/stream", json={
        "project_id": project_id, "section_id": "sec_1", "section_title": "第一章 总体方案",
    })
    last = _sse_events(res.text)[-1]
    assert last["done"] is False and "中断" in last["error"]

    node = find_node(project_store.get(project_id).outline, "sec_1")
    assert node.content == "原有正文"
    assert node.status == "reviewed"


class _Chunk:
    def __init__(self, text):
        self.choices = [SimpleNamespace(delta=SimpleNamespace(content=text))]


class _BrokenAsyncStream:
    """先输出一个真实分片，随后网络中断"""

    def __init__(self):
        self._sent = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._sent:
            self._sent = True
            return _Chunk("真实分片")
        raise ConnectionResetError("connection reset")


def test_async_stream_does_not_append_mock_after_partial_output(monkeypatch):
    async def create(**kwargs):
        return _BrokenAsyncStream()

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm_client, "is_configured", True)
    monkeypatch.setattr(llm_client, "async_client", fake_client)

    received = []

    async def consume_into():
        async for token in llm_client.chat_completion_stream_async("sys", "user"):
            received.append(token)

    with pytest.raises(RuntimeError, match="中断"):
        asyncio.run(consume_into())
    assert received == ["真实分片"], "中断后不应拼接离线模拟文本"
