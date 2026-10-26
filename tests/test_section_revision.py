"""阶段 C2：章节修订号与内容来源；大纲整树保存只合并结构，不覆盖正文"""
import pytest

from app.models.schemas import OutlineNode
from app.services.generator.section_generator import section_generator
from app.services.project_store import find_node, project_store
from app.services.section_content import save_section_content

OUTLINE = [
    {"id": "sec_1", "title": "第一章 方案", "level": 1, "children": [
        {"id": "sec_1_1", "title": "1.1 架构", "level": 2},
        {"id": "sec_1_2", "title": "1.2 运维", "level": 2},
    ]},
]


def _node(pid, sid):
    return find_node(project_store.get(pid).outline, sid)


def _save(client, pid, sid, content, status=None):
    body = {"section_id": sid, "content": content}
    if status:
        body["status"] = status
    assert client.put(f"/api/v1/project/{pid}/section", json=body).status_code == 200


def test_revision_counts_content_changes_and_records_source(client, project_id, monkeypatch):
    client.put(f"/api/v1/project/{project_id}/outline", json={"outline": OUTLINE})
    n = _node(project_id, "sec_1_1")
    assert (n.revision, n.content_source) == (0, "")

    _save(client, project_id, "sec_1_1", "人工稿")
    _save(client, project_id, "sec_1_1", "人工稿", status="reviewed")  # 只改状态：修订号不变
    n = _node(project_id, "sec_1_1")
    assert (n.revision, n.content_source, n.status) == (1, "manual", "reviewed")

    async def fake_stream(**kwargs):
        yield {"refs": [], "retrieval_message": "", "mode": "llm"}
        yield {"token": "AI稿"}

    monkeypatch.setattr(section_generator, "draft_section_stream", fake_stream)
    client.post(f"/api/v1/project/{project_id}/section/generate/stream",
                json={"project_id": project_id, "section_id": "sec_1_1", "section_title": "1.1 架构"})
    assert (_node(project_id, "sec_1_1").revision, _node(project_id, "sec_1_1").content_source) == (2, "ai_generate")

    client.post(f"/api/v1/project/{project_id}/section/polish",
                json={"project_id": project_id, "section_id": "sec_1_1", "content": "值得一提的是，AI稿润色", "polish_mode": "de_ai"})
    n = _node(project_id, "sec_1_1")
    assert n.content_source == "polish" and n.revision == 3

    versions = client.get(f"/api/v1/project/{project_id}/section/sec_1_1/versions").json()["versions"]
    manual = next(v for v in versions if v["preview"] == "人工稿")
    client.post(f"/api/v1/project/{project_id}/section/sec_1_1/versions/{manual['id']}/restore")
    n = _node(project_id, "sec_1_1")
    assert (n.content, n.revision, n.content_source) == ("人工稿", 4, "restore")


def test_outline_save_merges_structure_and_keeps_server_content(client, project_id):
    client.put(f"/api/v1/project/{project_id}/outline", json={"outline": OUTLINE})
    _save(client, project_id, "sec_1_1", "后台写入的正文", status="completed")
    client.put(f"/api/v1/project/{project_id}/section/refs",
               json={"section_id": "sec_1_1", "pinned_refs": ["c1"], "excluded_refs": []})

    # 客户端拿着旧快照（正文为空、修订号 0）改了标题、字数预算，删掉 1.2、新增 1.3
    stale = [{"id": "sec_1", "title": "第一章 总体方案", "level": 1, "children": [
        {"id": "sec_1_1", "title": "1.1 总体架构", "level": 2, "word_budget": 800, "content": "",
         "status": "pending", "revision": 0, "requirements": ["新要求"]},
        {"id": "sec_1_3", "title": "1.3 新增", "level": 2, "content": "客户端新建的正文", "revision": 7},
    ]}]
    res = client.put(f"/api/v1/project/{project_id}/outline", json={"outline": stale})
    assert res.status_code == 200 and res.json()["removed"] == ["sec_1_2"]

    n = _node(project_id, "sec_1_1")
    assert (n.title, n.word_budget, n.requirements) == ("1.1 总体架构", 800, ["新要求"])
    assert (n.content, n.status, n.revision, n.pinned_refs) == ("后台写入的正文", "completed", 1, ["c1"])
    new = _node(project_id, "sec_1_3")
    assert (new.content, new.revision, new.content_source) == ("客户端新建的正文", 0, "manual")
    assert _node(project_id, "sec_1_2") is None
    returned = res.json()["outline"][0]["children"][0]
    assert returned["content"] == "后台写入的正文"


def test_deviation_inject_counts_as_revision_and_keeps_manual_draft(client, project_id):
    client.put(f"/api/v1/project/{project_id}/outline", json={"outline": OUTLINE})
    _save(client, project_id, "sec_1_2", "人工写的应答说明")
    client.put(f"/api/v1/project/{project_id}/deviation",
               json=[{"index": 1, "clause_title": "支持国产数据库", "response_status": "完全满足", "response_detail": "支持"}])
    assert client.post(f"/api/v1/project/{project_id}/deviation/inject", json={"section_id": "sec_1_2"}).status_code == 200

    n = _node(project_id, "sec_1_2")
    assert "支持国产数据库" in n.content and n.content_mode == "point_to_point"
    assert (n.revision, n.content_source) == (2, "deviation")
    versions = client.get(f"/api/v1/project/{project_id}/section/sec_1_2/versions").json()["versions"]
    assert [v["source"] for v in versions] == ["deviation", "manual"] and versions[1]["preview"] == "人工写的应答说明"
    assert client.post(f"/api/v1/project/{project_id}/deviation/inject", json={"section_id": "nope"}).status_code == 404


def test_review_binds_revision_and_changes_require_explicit_rereview(client, project_id):
    client.put(f"/api/v1/project/{project_id}/outline", json={"outline": OUTLINE})
    url = f"/api/v1/project/{project_id}/section"
    body = {"section_id": "sec_1_1", "content": "已核对正文"}
    assert client.put(url, json=body).json()["revision"] == 1
    reviewed = client.put(url, json={**body, "status": "reviewed", "base_revision": 1}).json()
    assert reviewed["node_status"] == "reviewed" and reviewed["reviewed_revision"] == 1
    assert client.put(url, json={**body, "status": "completed"}).json()["node_status"] == "reviewed"
    changed = {**body, "content": "修改后的正文"}
    saved = client.put(url, json={**changed, "status": "reviewed"}).json()  # 兼容旧客户端沿用 reviewed
    assert (saved["node_status"], saved["revision"], saved["reviewed_revision"]) == ("completed", 2, None)
    report = client.get(f"/api/v1/project/{project_id}/export/preflight").json()
    check = next(c for c in report["checks"] if c["key"] == "unreviewed")
    assert check["status"] == "warn" and check["details"][0]["section_id"] == body["section_id"]
    assert client.put(url, json={**body, "status": "reviewed", "base_revision": 1}).status_code == 409
    assert _node(project_id, "sec_1_1").content == "修改后的正文"
    confirmed = client.put(url, json={**changed, "status": "reviewed", "base_revision": 2}).json()
    assert confirmed["node_status"] == "reviewed" and confirmed["reviewed_revision"] == 2


@pytest.mark.parametrize("source", ["ai_generate", "batch", "polish", "restore", "proposal", "deviation"])
def test_all_content_writers_invalidate_previous_review(client, project_id, source):
    client.put(f"/api/v1/project/{project_id}/outline", json={"outline": OUTLINE})
    _save(client, project_id, "sec_1_1", "原稿")
    _save(client, project_id, "sec_1_1", "原稿", "reviewed")
    save_section_content(project_id, "sec_1_1", "新稿", "reviewed", content_source=source)
    n = _node(project_id, "sec_1_1")
    assert (n.status, n.revision, n.reviewed_revision) == ("completed", 2, None)


def test_legacy_review_is_bound_on_read_and_parent_stale_review_is_reported(project_id):
    from app.services.checker.export_preflight import build_preflight
    node = OutlineNode(id="parent", title="父章", content="正文", status="reviewed", revision=7,
                       children=[OutlineNode(id="child", title="子章")])
    assert node.reviewed_revision == 7
    node.revision = 8
    project = project_store.get(project_id)
    project.outline = [node]
    check = next(c for c in build_preflight(project)["checks"] if c["key"] == "unreviewed")
    assert check["details"][0]["section_id"] == "parent"
