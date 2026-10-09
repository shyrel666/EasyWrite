"""R03：同章生成/润色只能写回任务开始时的修订与校审状态，冲突保留候选稿。"""
import json

import pytest

from app.api import sections
from app.models.schemas import PolishSectionResponse
from app.services.generator.evidence_set import EvidenceSet
from app.services.project_store import find_node, project_store
from app.services.proposals import proposal_store, propose


@pytest.fixture()
def section(client, project_id):
    client.put(f"/api/v1/project/{project_id}/outline", json={"outline": [{"id": "s1", "title": "方案"}]})
    saved = client.put(f"/api/v1/project/{project_id}/section", json={"section_id": "s1", "content": "人工原稿"})
    assert saved.json()["revision"] == 1
    return project_id


@pytest.mark.parametrize("endpoint", ["generate", "generate/stream", "polish"])
@pytest.mark.parametrize("edit", ["content", "reviewed", "reverted"])
def test_same_section_conflict_preserves_human_text_and_ai_candidate(client, section, monkeypatch, endpoint, edit):
    pid = section

    def change():
        content = "人工原稿" if edit == "reviewed" else "并发人工修改"
        res = client.put(f"/api/v1/project/{pid}/section", json={
            "section_id": "s1", "content": content, "status": "reviewed"})
        assert res.status_code == 200
        if edit == "reverted":
            client.put(f"/api/v1/project/{pid}/section", json={
                "section_id": "s1", "content": "人工原稿", "status": "completed"})

    def draft(**kw):
        change()
        return {"generated_content": "AI结果", "references": [], "mode": "llm"}

    async def stream(**kw):
        yield {"refs": [], "mode": "llm"}
        yield {"token": "AI"}
        change()
        yield {"token": "结果"}

    def polish(req, facts):
        change()
        return PolishSectionResponse(section_id="s1", original_content=req.content, polished_content="AI结果")

    monkeypatch.setattr(sections.section_generator, "draft_section", draft)
    monkeypatch.setattr(sections.section_generator, "draft_section_stream", stream)
    monkeypatch.setattr(sections.quality_inspector, "polish_section", polish)
    res = client.post(f"/api/v1/project/{pid}/section/{endpoint}", json={
        "project_id": pid, "section_id": "s1", "section_title": "方案", "content": "人工原稿", "base_revision": 1})
    if endpoint.endswith("stream"):
        events = [json.loads(line[6:]) for line in res.text.splitlines() if line.startswith("data: ")]
        detail = events[-1]
        assert detail["done"] is False and detail["status_code"] == 409
        assert not any(event.get("done") is True for event in events)
    else:
        assert res.status_code == 409
        detail = res.json()["detail"]
    node = find_node(project_store.get(pid).outline, "s1")
    assert node.content == ("并发人工修改" if edit == "content" else "人工原稿")
    assert node.status == ("completed" if edit == "reverted" else "reviewed")
    assert detail["reason"] == "content_changed" and detail["current_revision"] == node.revision
    proposal = proposal_store.get(pid, detail["proposal_id"])
    assert proposal.content == "AI结果" and proposal.base_revision == 1
    assert client.get(f"/api/v1/project/{pid}/section/s1/versions").json()["versions"] == []
    if edit != "reviewed":
        assert client.post(f"/api/v1/project/{pid}/section/s1/proposals/{proposal.id}/apply").status_code == 409
        assert find_node(project_store.get(pid).outline, "s1").content == node.content


@pytest.mark.parametrize("endpoint", ["generate", "generate/stream", "polish"])
def test_stale_request_rejected_before_calling_model(client, section, monkeypatch, endpoint):
    def unexpected(*args, **kwargs):
        pytest.fail("过期请求不应开始生成")

    monkeypatch.setattr(sections, "select_evidence", unexpected)
    res = client.post(f"/api/v1/project/{section}/section/{endpoint}", json={
        "project_id": section, "section_id": "s1", "section_title": "方案", "content": "旧稿", "base_revision": 0})
    assert res.status_code == 409
    assert res.json()["detail"]["current_revision"] == 1


def test_legacy_request_still_protected_by_server_snapshot(client, section, monkeypatch):
    def draft(**kw):
        client.put(f"/api/v1/project/{section}/section", json={"section_id": "s1", "content": "另一页的修改"})
        return {"generated_content": "AI结果", "references": []}

    monkeypatch.setattr(sections.section_generator, "draft_section", draft)
    res = client.post(f"/api/v1/project/{section}/section/generate", json={
        "project_id": section, "section_id": "s1", "section_title": "方案"})
    assert res.status_code == 409


def test_conflict_response_can_precede_a_newer_manual_revision(client, section, monkeypatch):
    """冲突检测与响应返回之间还可保存；前端必须按修订号拒绝较旧响应。"""
    pid = section

    def save(content):
        result = client.put(f"/api/v1/project/{pid}/section", json={"section_id": "s1", "content": content})
        assert result.status_code == 200
        return result.json()["revision"]

    def polish(req, facts):
        assert save("第一轮人工修改") == 2
        return PolishSectionResponse(section_id="s1", original_content=req.content, polished_content="AI结果")

    original_propose = sections.propose

    def propose_after_conflict(*args, **kwargs):
        result = original_propose(*args, **kwargs)
        assert save("人工原稿") == 3
        return result

    monkeypatch.setattr(sections.quality_inspector, "polish_section", polish)
    monkeypatch.setattr(sections, "propose", propose_after_conflict)
    response = client.post(f"/api/v1/project/{pid}/section/polish", json={
        "project_id": pid, "section_id": "s1", "content": "人工原稿", "base_revision": 1})
    assert response.status_code == 409
    detail = response.json()["detail"]
    node = find_node(project_store.get(pid).outline, "s1")
    assert (detail["current_content"], detail["current_revision"]) == ("第一轮人工修改", 2)
    assert (node.content, node.revision) == ("人工原稿", 3)
    assert proposal_store.get(pid, detail["proposal_id"]).content == "AI结果"


def test_conflict_candidate_keeps_other_open_proposals(client, section, monkeypatch):
    """冲突时留存的 AI 结果只供查看，不能把本节仍可采纳的候选稿（如智能完善结果）标为已取代。"""
    pid = section
    project = project_store.get(pid)
    kept = propose(project, find_node(project.outline, "s1"), "智能完善候选", EvidenceSet(), origin="refine")

    def polish(req, facts):
        client.put(f"/api/v1/project/{pid}/section", json={"section_id": "s1", "content": "人工原稿", "status": "reviewed"})
        return PolishSectionResponse(section_id="s1", original_content=req.content, polished_content="AI结果")

    monkeypatch.setattr(sections.quality_inspector, "polish_section", polish)
    res = client.post(f"/api/v1/project/{pid}/section/polish", json={
        "project_id": pid, "section_id": "s1", "content": "人工原稿", "base_revision": 1})
    assert res.status_code == 409
    assert proposal_store.get(pid, kept["id"]).status == kept["status"]
    open_ids = {p.id for p in proposal_store.list(pid, "s1", open_only=True)}
    assert open_ids == {kept["id"], res.json()["detail"]["proposal_id"]}


def test_polish_result_stays_in_history_when_overwritten_by_pending_input(client, section, monkeypatch):
    """润色写入时把润色稿本身存为历史版本：随后保存的人工输入（润色期间尚在防抖中）覆盖它也能找回。"""
    pid = section
    monkeypatch.setattr(sections.quality_inspector, "polish_section", lambda req, facts: PolishSectionResponse(
        section_id="s1", original_content=req.content, polished_content="润色结果"))
    res = client.post(f"/api/v1/project/{pid}/section/polish", json={
        "project_id": pid, "section_id": "s1", "content": "人工原稿", "base_revision": 1})
    assert res.status_code == 200 and res.json()["revision"] == 2
    assert client.put(f"/api/v1/project/{pid}/section", json={"section_id": "s1", "content": "润色期间的人工输入"}).status_code == 200
    versions = client.get(f"/api/v1/project/{pid}/section/s1/versions").json()["versions"]
    assert ("polish", "润色结果") in [(v["source"], v["preview"]) for v in versions]
