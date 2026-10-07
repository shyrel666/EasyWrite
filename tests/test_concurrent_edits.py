"""
并发编辑不丢稿回归测试：
- 流式生成期间其他章节的自动保存不被旧快照覆盖
- 多线程并发写不同章节全部落库
- 偏离表批量生成不覆盖人工填写的响应
"""
import json
import threading
import time

from app.services.generator.section_generator import section_generator
from app.services.project_store import project_store, find_node
from conftest import TENDER_SAMPLE

OUTLINE = [
    {
        "id": "sec_1", "title": "第一章 总体方案", "level": 1,
        "children": [
            {"id": "sec_1_1", "title": "1.1 架构设计", "level": 2},
            {"id": "sec_1_2", "title": "1.2 安全设计", "level": 2},
        ],
    },
    {"id": "sec_2", "title": "第二章 运维保障", "level": 1},
]


def _setup_outline(client, pid):
    res = client.put(f"/api/v1/project/{pid}/outline", json={"outline": OUTLINE})
    assert res.status_code == 200


def _sse_events(text: str):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_stream_save_keeps_concurrent_edit(client, project_id, monkeypatch):
    _setup_outline(client, project_id)
    fake_refs = [{"chunk_id": "c1", "doc_name": "历史标书", "content": "参考"}]

    async def fake_stream(**kwargs):
        yield {"refs": fake_refs, "retrieval_message": "", "mode": "mock"}
        # 流式进行中：用户在另一章节触发自动保存
        res = client.put(f"/api/v1/project/{project_id}/section", json={
            "project_id": project_id, "section_id": "sec_1_2",
            "content": "用户在流式期间编辑的安全设计", "status": "reviewed",
        })
        assert res.status_code == 200
        for token in ("生成的", "架构设计", "正文"):
            yield {"token": token}

    monkeypatch.setattr(section_generator, "draft_section_stream", fake_stream)
    res = client.post(f"/api/v1/project/{project_id}/section/generate/stream", json={
        "project_id": project_id, "section_id": "sec_1_1", "section_title": "1.1 架构设计",
    })
    assert res.status_code == 200
    assert _sse_events(res.text)[-1]["done"] is True

    project = project_store.get(project_id)
    generated = find_node(project.outline, "sec_1_1")
    edited = find_node(project.outline, "sec_1_2")
    assert edited.content == "用户在流式期间编辑的安全设计", "流式写回覆盖了并发编辑"
    assert edited.status == "reviewed"
    assert generated.content == "生成的架构设计正文"
    assert generated.last_refs == fake_refs, "生成引用应随正文持久化"


def test_stream_unknown_section_rejected_before_generation(client, project_id):
    _setup_outline(client, project_id)
    res = client.post(f"/api/v1/project/{project_id}/section/generate/stream", json={
        "project_id": project_id, "section_id": "sec_missing", "section_title": "不存在",
    })
    assert res.status_code == 404


def test_parallel_section_updates_all_persist(client, project_id):
    _setup_outline(client, project_id)
    section_ids = ["sec_1", "sec_1_1", "sec_1_2", "sec_2"]

    def write(section_id: str, round_no: int):
        def mutate(project):
            node = find_node(project.outline, section_id)
            time.sleep(0.005)  # 放大读-改-写窗口
            node.content = f"{section_id}-{round_no}"
        project_store.update(project_id, mutate)

    threads = [
        threading.Thread(target=write, args=(sid, r))
        for r in range(5) for sid in section_ids
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    project = project_store.get(project_id)
    for sid in section_ids:
        assert find_node(project.outline, sid).content.startswith(f"{sid}-")


def test_unknown_project_returns_404(client):
    res = client.put("/api/v1/project/proj_missing/facts", json={})
    assert res.status_code == 404
    assert res.json()["detail"] == "项目不存在"


def test_batch_deviation_keeps_manual_responses(client, project_id):
    res = client.post(f"/api/v1/project/{project_id}/deviation/extract",
                      json={"tender_content": TENDER_SAMPLE})
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) >= 2
    items[0]["response_status"] = "完全满足"
    items[0]["response_detail"] = "人工填写的点对点响应"
    assert client.put(f"/api/v1/project/{project_id}/deviation", json=items).status_code == 200

    res = client.post(f"/api/v1/project/{project_id}/deviation/generate")
    assert res.status_code == 200
    assert res.json()["skipped_count"] == 1
    task_id = res.json()["task_id"]
    for _ in range(50):
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        if task["status"] in ("completed", "failed"):
            break
        time.sleep(0.1)
    assert task["status"] == "completed", task.get("error")

    stored = project_store.get(project_id).deviation_matrix
    assert stored[0].response_status == "完全满足"
    assert stored[0].response_detail == "人工填写的点对点响应"
    # 离线模式：其余条目保持待生成，不伪造响应
    assert all(it.response_status == "待生成" for it in stored[1:])
