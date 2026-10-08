"""
阶段 C 验收（经批量撰写产生候选稿的完整路径）：
- 候选稿等待采纳期间，用户手改正文 → 采纳返回"正文已变化"；修改全局事实 → 返回"依据已变化"
- 同一候选稿连续采纳两次，正文和历史版本都只写入一次
- 写入正文之后、提交之前出现异常，正文、历史版本和候选稿状态都保持原样
"""
import time

import pytest

from app.core.llm_client import llm_client
from app.services.generator.section_generator import section_generator
from app.services.project_store import find_node, project_store
from app.services.proposals import proposal_store
from app.services.version_store import version_store

OUTLINE = [{"id": "sec_1", "title": "第一章 运维方案", "level": 1, "children": [
    {"id": "sec_1_1", "title": "1.1 故障响应", "level": 2, "content": "人工稿", "status": "completed"}]}]


@pytest.fixture()
def pid(client, project_id, monkeypatch):
    client.put(f"/api/v1/project/{project_id}/outline", json={"outline": OUTLINE})
    monkeypatch.setattr(llm_client, "is_configured", True)
    monkeypatch.setattr(section_generator, "draft_section",
                        lambda **kw: {"generated_content": "AI 重写稿", "references": [], "mode": "llm"})
    return project_id


def _batch_proposal(client, pid):
    task_id = client.post(f"/api/v1/project/{pid}/sections/generate-batch",
                          json={"include_written": True}).json()["task_id"]
    for _ in range(200):
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        if task["status"] not in ("pending", "running"):
            break
        time.sleep(0.02)
    assert task["status"] == "completed" and task["result"]["generated"] == []
    return task["result"]["proposed"][0]["proposal_id"]


def _apply(client, pid, proposal_id):
    return client.post(f"/api/v1/project/{pid}/section/sec_1_1/proposals/{proposal_id}/apply")


def _content(pid):
    return find_node(project_store.get(pid).outline, "sec_1_1").content


def test_edit_or_facts_change_while_waiting_blocks_apply(client, pid):
    proposal_id = _batch_proposal(client, pid)
    assert _content(pid) == "人工稿"  # 批量撰写没有覆盖已有正文
    client.put(f"/api/v1/project/{pid}/section", json={"section_id": "sec_1_1", "content": "人工稿（又改了一句）"})
    res = _apply(client, pid, proposal_id)
    assert res.status_code == 409 and res.json()["detail"]["reason"] == "content_changed"

    proposal_id = _batch_proposal(client, pid)
    client.put(f"/api/v1/project/{pid}/facts", json={"sla_commitment": "接到报修后30分钟内响应"})
    res = _apply(client, pid, proposal_id)
    assert res.status_code == 409 and res.json()["detail"]["reason"] == "basis_changed"
    assert _content(pid) == "人工稿（又改了一句）"


def test_double_apply_writes_once(client, pid):
    proposal_id = _batch_proposal(client, pid)
    assert _apply(client, pid, proposal_id).json()["already_applied"] is False
    assert _apply(client, pid, proposal_id).json()["already_applied"] is True
    assert _content(pid) == "AI 重写稿"
    assert [v["source"] for v in version_store.list(pid, "sec_1_1")] == ["proposal", "manual"]
    assert find_node(project_store.get(pid).outline, "sec_1_1").revision == 1


def test_failure_before_commit_keeps_everything(client, pid, monkeypatch):
    proposal_id = _batch_proposal(client, pid)

    def broken(*args, **kwargs):
        raise RuntimeError("版本写入失败")

    monkeypatch.setattr(version_store, "record_in", broken)
    with pytest.raises(RuntimeError):
        _apply(client, pid, proposal_id)
    assert _content(pid) == "人工稿"
    assert version_store.list(pid, "sec_1_1") == []
    assert proposal_store.get(pid, proposal_id).status == "checked"
