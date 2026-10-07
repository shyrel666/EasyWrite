"""后台任务落库：结束后可查、重启后未结束任务标记为已中断、按项目归属与筛选。"""
import threading
import time
import uuid

import app.core.task_manager as tm
from app.core.task_manager import INTERRUPTED_MESSAGE, task_manager
from app.db.database import get_session
from app.db.models import KBDocument, TaskModel
from app.services.rag.ingestor import mark_interrupted_ingests


def _wait(task_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = task_manager.get(task_id)
        if task and task["status"] not in ("pending", "running"):
            return task
        time.sleep(0.05)
    raise AssertionError(f"任务 {task_id} 未在 {timeout}s 内结束")


def _insert_row(status: str, **kw) -> str:
    task_id = f"task_test_{uuid.uuid4().hex[:8]}"
    with get_session() as session:
        session.add(TaskModel(id=task_id, type=kw.pop("type", "section_batch"), status=status,
                              created_ts=kw.pop("created_ts", time.time()), **kw))
    return task_id


def test_finished_task_is_read_back_from_database():
    task_id = task_manager.submit("unit_test", lambda ctx: {"answer": 42, "名称": "中文"},
                                  description="单元测试任务", project_id="proj_x")
    task = _wait(task_id)
    assert task["status"] == "completed" and task["progress"] == 100
    # 结束后内存记录已释放，结果来自 tasks 表
    assert task_id not in task_manager._tasks
    assert task["result"] == {"answer": 42, "名称": "中文"}
    assert task["title"] == "单元测试任务" and task["project_id"] == "proj_x"
    with get_session() as session:
        row = session.get(TaskModel, task_id)
        assert row.status == "completed" and '"answer": 42' in row.result_json


def test_failed_and_cancelled_tasks_are_persisted():
    def boom(ctx):
        raise ValueError("解析失败：示例错误")

    failed = _wait(task_manager.submit("unit_test", boom))
    assert failed["status"] == "failed" and "解析失败：示例错误" in failed["error"]

    started = threading.Event()

    def slow(ctx):
        started.set()
        while not ctx.cancelled():
            time.sleep(0.02)
        return {"partial": True}

    task_id = task_manager.submit("unit_test", slow)
    assert started.wait(5)
    assert task_manager.cancel(task_id)
    cancelled = _wait(task_id)
    assert cancelled["status"] == "cancelled" and cancelled["result"] == {"partial": True}
    # 已结束的任务不可再取消
    assert not task_manager.cancel(task_id)


def test_restart_marks_unfinished_tasks_interrupted_but_spares_live_ones(client):
    stale_running = _insert_row("running", progress=40, message="撰写 3/10")
    stale_pending = _insert_row("pending")
    done = _insert_row("completed", progress=100)

    release = threading.Event()
    live_id = task_manager.submit("unit_test", lambda ctx: release.wait(5))
    try:
        marked = task_manager.recover_interrupted()
        assert marked >= 2
        for task_id in (stale_running, stale_pending):
            task = client.get(f"/api/v1/tasks/{task_id}").json()
            assert task["status"] == "interrupted"
            assert task["error"] == INTERRUPTED_MESSAGE
        assert client.get(f"/api/v1/tasks/{stale_running}").json()["progress"] == 40
        assert client.get(f"/api/v1/tasks/{done}").json()["status"] == "completed"
        # 本进程正在跑的任务不受影响
        assert task_manager.get(live_id)["status"] in ("pending", "running")
    finally:
        release.set()
    assert _wait(live_id)["status"] == "completed"


def test_list_overlays_live_progress_and_filters(client):
    release = threading.Event()
    reported = threading.Event()

    def work(ctx):
        ctx.report(55, "处理中段")  # 距开始落库不足节流间隔：库中仍是旧快照
        reported.set()
        release.wait(5)
        return {"ok": True}

    task_id = task_manager.submit("unit_list", work, project_id="proj_list")
    try:
        assert reported.wait(5)
        active = client.get("/api/v1/tasks", params={"project_id": "proj_list", "active": True}).json()["tasks"]
        assert [t["id"] for t in active] == [task_id]
        assert active[0]["progress"] == 55 and active[0]["message"] == "处理中段"
    finally:
        release.set()
    _wait(task_id)
    tasks = client.get("/api/v1/tasks", params={"project_id": "proj_list", "type": "unit_list"}).json()["tasks"]
    assert tasks[0]["id"] == task_id and tasks[0]["status"] == "completed"
    assert tasks[0]["result"] is None  # 列表默认不带结果载荷
    with_result = client.get("/api/v1/tasks", params={"project_id": "proj_list", "with_result": True}).json()["tasks"]
    assert with_result[0]["result"] == {"ok": True}
    assert client.get("/api/v1/tasks", params={"project_id": "proj_list", "active": True}).json()["tasks"] == []


def test_task_inherits_project_from_request_url(client, project_id):
    res = client.post(f"/api/v1/project/{project_id}/compliance/check")
    assert res.status_code == 200
    task = _wait(res.json()["task_id"])
    assert task["status"] == "completed"
    assert task["project_id"] == project_id
    listed = client.get("/api/v1/tasks", params={"project_id": project_id}).json()["tasks"]
    assert any(t["id"] == task["id"] and t["type"] == "compliance_check" for t in listed)


def test_global_tasks_have_no_project(client, tmp_path):
    from conftest import build_sample_bid_docx

    path = build_sample_bid_docx(tmp_path / "历史标书.docx")
    with path.open("rb") as f:
        res = client.post("/api/v1/knowledge/upload?curate=false",
                          files={"file": ("历史标书.docx", f, "application/octet-stream")})
    task = _wait(res.json()["task_id"], timeout=30)
    assert task["status"] == "completed" and task["project_id"] == ""
    client.delete(f"/api/v1/knowledge/documents/{task['result']['doc_id']}")


def test_prune_keeps_only_recent_finished_tasks(monkeypatch):
    monkeypatch.setattr(tm, "KEEP_FINISHED", 3)
    future = time.time() + 10_000
    ids = [_insert_row("completed", created_ts=future + i) for i in range(5)]
    running = _insert_row("running", created_ts=future - 1)
    task_manager.recover_interrupted()
    with get_session() as session:
        remaining = {tid for tid in ids if session.get(TaskModel, tid)}
        assert remaining == set(ids[-3:])
        # 先标记为已中断，再与其他已结束任务一起按新旧裁剪
        assert session.get(TaskModel, running) is None
        for tid in ids[-3:]:
            session.delete(session.get(TaskModel, tid))


def test_interrupted_ingest_documents_are_marked_failed():
    doc_id = f"kbdoc_test_{uuid.uuid4().hex[:8]}"
    with get_session() as session:
        session.add(KBDocument(id=doc_id, doc_name="未完成.docx", status="ingesting"))
    assert mark_interrupted_ingests() >= 1
    with get_session() as session:
        doc = session.get(KBDocument, doc_id)
        assert doc.status == "error" and "重启" in doc.error_msg
        session.delete(doc)


def test_unknown_task_is_404(client):
    assert client.get("/api/v1/tasks/task_not_exist").status_code == 404
