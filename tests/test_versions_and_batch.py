"""
章节历史版本与批量撰写：
- AI 覆盖前的人工稿自动留版；人工自动保存不逐次留版；恢复可回到任意版本；单章保留上限
- 批量撰写：离线拒绝；只写 AI 模式的空白叶节点，跳过已校审；生成期间用户改过的章节不覆盖；
  模型失败退回演示样例时不写入
"""
import time

from app.core.llm_client import llm_client
from app.services import version_store as vs_module
from app.services.generator.section_generator import section_generator
from app.services.project_store import find_node, project_store
from app.services.version_store import version_store

OUTLINE = [
    {"id": "sec_1", "title": "第一章 总体方案", "level": 1, "children": [
        {"id": "sec_1_1", "title": "1.1 架构设计", "level": 2},
        {"id": "sec_1_2", "title": "1.2 安全设计", "level": 2, "content": "已有人工稿", "status": "completed"},
        {"id": "sec_1_3", "title": "1.3 运维保障", "level": 2, "content": "已校审定稿", "status": "reviewed"},
        {"id": "sec_1_4", "title": "1.4 偏离应答", "level": 2, "content_mode": "point_to_point"},
    ]},
]


def _setup(client, pid):
    assert client.put(f"/api/v1/project/{pid}/outline", json={"outline": OUTLINE}).status_code == 200


def _wait(client, task_id):
    for _ in range(100):
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        if task["status"] in ("completed", "failed", "cancelled"):
            return task
        time.sleep(0.05)
    raise AssertionError("任务超时")


def _content(pid, sid):
    return find_node(project_store.get(pid).outline, sid).content


def test_versions_snapshot_manual_draft_before_ai_overwrite(client, project_id, monkeypatch):
    _setup(client, project_id)
    for text in ("人工稿v1", "人工稿v2"):  # 自动保存不逐次留版
        client.put(f"/api/v1/project/{project_id}/section",
                   json={"project_id": project_id, "section_id": "sec_1_1", "content": text, "status": "completed"})
    assert client.get(f"/api/v1/project/{project_id}/section/sec_1_1/versions").json()["versions"] == []

    async def fake_stream(**kwargs):
        yield {"refs": [], "retrieval_message": "", "mode": "llm"}
        yield {"token": "AI生成稿"}

    monkeypatch.setattr(section_generator, "draft_section_stream", fake_stream)
    client.post(f"/api/v1/project/{project_id}/section/generate/stream",
                json={"project_id": project_id, "section_id": "sec_1_1", "section_title": "1.1 架构设计"})
    versions = client.get(f"/api/v1/project/{project_id}/section/sec_1_1/versions").json()["versions"]
    assert [(v["source"], v["preview"]) for v in versions] == [("ai_generate", "AI生成稿"), ("manual", "人工稿v2")]

    manual_id = versions[1]["id"]
    res = client.post(f"/api/v1/project/{project_id}/section/sec_1_1/versions/{manual_id}/restore")
    assert res.status_code == 200 and res.json()["content"] == "人工稿v2"
    assert _content(project_id, "sec_1_1") == "人工稿v2"
    versions = client.get(f"/api/v1/project/{project_id}/section/sec_1_1/versions").json()["versions"]
    assert [v["source"] for v in versions] == ["restore", "ai_generate", "manual"]
    detail = client.get(f"/api/v1/project/{project_id}/section/sec_1_1/versions/{versions[1]['id']}").json()
    assert detail["content"] == "AI生成稿"
    assert client.get(f"/api/v1/project/{project_id}/section/sec_1_2/versions/{manual_id}").status_code == 404


def test_versions_pruned_per_section(project_id, monkeypatch):
    monkeypatch.setattr(vs_module, "MAX_VERSIONS_PER_SECTION", 3)
    for i in range(5):
        version_store.record(project_id, "sec_x", f"v{i}", f"v{i + 1}", "ai_generate")
    assert [v["preview"] for v in version_store.list(project_id, "sec_x")] == ["v5", "v4", "v3"]


def test_batch_requires_llm(client, project_id, monkeypatch):
    _setup(client, project_id)
    monkeypatch.setattr(llm_client, "is_configured", False)
    res = client.post(f"/api/v1/project/{project_id}/sections/generate-batch", json={})
    assert res.status_code == 400 and "配置大模型" in res.json()["detail"]


def _fake_llm(monkeypatch, draft):
    monkeypatch.setattr(llm_client, "is_configured", True)
    monkeypatch.setattr(section_generator, "draft_section", draft)


def test_batch_writes_only_eligible_sections(client, project_id, monkeypatch):
    _setup(client, project_id)
    seen = []

    def draft(**kw):
        seen.append(kw["section_id"])
        return {"generated_content": f"批量稿：{kw['section_title']}", "references": [], "mode": "llm"}

    _fake_llm(monkeypatch, draft)
    res = client.post(f"/api/v1/project/{project_id}/sections/generate-batch", json={})
    assert res.json()["total"] == 1
    task = _wait(client, res.json()["task_id"])
    assert task["result"]["generated"] == ["sec_1_1"] and seen == ["sec_1_1"]
    assert _content(project_id, "sec_1_1") == "批量稿：1.1 架构设计"
    assert _content(project_id, "sec_1_2") == "已有人工稿"
    assert _content(project_id, "sec_1_3") == "已校审定稿"

    # 包含已有内容：覆盖未校审的人工稿（覆盖前留版），已校审仍跳过
    res = client.post(f"/api/v1/project/{project_id}/sections/generate-batch", json={"include_written": True})
    task = _wait(client, res.json()["task_id"])
    assert sorted(task["result"]["generated"]) == ["sec_1_1", "sec_1_2"]
    assert _content(project_id, "sec_1_3") == "已校审定稿"
    sources = [v["source"] for v in version_store.list(project_id, "sec_1_2")]
    assert sources == ["batch", "manual"]


def test_batch_keeps_user_edit_made_during_generation(client, project_id, monkeypatch):
    _setup(client, project_id)

    def draft(**kw):
        client.put(f"/api/v1/project/{project_id}/section", json={
            "project_id": project_id, "section_id": kw["section_id"], "content": "用户抢先写的", "status": "completed"})
        return {"generated_content": "批量稿", "references": [], "mode": "llm"}

    _fake_llm(monkeypatch, draft)
    task = _wait(client, client.post(f"/api/v1/project/{project_id}/sections/generate-batch", json={}).json()["task_id"])
    assert task["result"]["skipped"] == ["sec_1_1"] and task["result"]["generated"] == []
    assert _content(project_id, "sec_1_1") == "用户抢先写的"


def test_batch_does_not_save_mock_fallback(client, project_id, monkeypatch):
    _setup(client, project_id)
    _fake_llm(monkeypatch, lambda **kw: {"generated_content": "演示样例", "references": [], "mode": "mock"})
    task = _wait(client, client.post(f"/api/v1/project/{project_id}/sections/generate-batch", json={}).json()["task_id"])
    assert task["result"]["failed"][0]["id"] == "sec_1_1"
    assert _content(project_id, "sec_1_1") == ""
