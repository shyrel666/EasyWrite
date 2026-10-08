"""阶段 C3/C4：候选稿表、输入清单、采纳与放弃（正文或依据变化时 409；重复采纳只写一次；异常整体回滚）"""
import time

import pytest

from app.core.llm_client import llm_client
from app.db.database import get_session
from app.db.models import SectionProposal
from app.services import proposals as prop_module
from app.services.generator.evidence_set import EvidenceSet
from app.services.generator.section_generator import section_generator
from app.services.project_store import find_node, project_store
from app.services.proposals import proposal_store, propose

OUTLINE = [{"id": "sec_1", "title": "第一章 方案", "level": 1, "children": [
    {"id": "sec_1_1", "title": "1.1 架构", "level": 2, "word_budget": 500},
    {"id": "sec_1_2", "title": "1.2 运维", "level": 2},
]}]
ASSET = {"kind": "personnel", "asset_id": "p_li", "name": "李工", "status": "unverified", "source": "matched"}


@pytest.fixture()
def pid(client, project_id):
    client.put(f"/api/v1/project/{project_id}/outline", json={"outline": OUTLINE})
    client.post("/api/v1/assets/personnel", json={"id": "p_li", "name": "李工", "role": "项目经理", "status": "unverified"})
    _save(client, project_id, "sec_1_1", "人工初稿")
    return project_id


def _save(client, pid, sid, content):
    assert client.put(f"/api/v1/project/{pid}/section", json={"section_id": sid, "content": content}).status_code == 200


def _propose(pid, sid="sec_1_1", content="候选稿正文", assets=(ASSET,)):
    project = project_store.get(pid)
    evidence = EvidenceSet(refs=[{"chunk_id": "c1", "content": "参考"}], assets=list(assets))
    return propose(project, find_node(project.outline, sid), content, evidence, origin="batch")


def _url(pid, prop, action=""):
    base = f"/api/v1/project/{pid}/section/{prop['section_id']}/proposals/{prop['id']}"
    return f"{base}/{action}" if action else base


def _node(pid, sid="sec_1_1"):
    return find_node(project_store.get(pid).outline, sid)


def _versions(client, pid, sid="sec_1_1"):
    return client.get(f"/api/v1/project/{pid}/section/{sid}/versions").json()["versions"]


def test_propose_records_check_manifest_and_supersedes_older(client, pid):
    first = _propose(pid)
    assert first["status"] == "checked" and first["base_revision"] == 1
    assert first["report"]["section_id"] == "sec_1_1" and first["blocking_count"] == 0
    assert set(first["input_manifest"]) == {"facts", "scoring_items", "evidence_links", "ref_settings", "section",
                                            "asset:personnel:p_li"}
    assert [e["ref_type"] for e in first["evidence"]] == ["kb", "asset"]

    second = _propose(pid, content="第二版")
    assert proposal_store.get(pid, first["id"]).status == "superseded"
    data = client.get(f"/api/v1/project/{pid}/proposals").json()
    assert data["counts"] == {"sec_1_1": 1} and [i["id"] for i in data["items"]] == [second["id"]]
    items = client.get(f"/api/v1/project/{pid}/section/sec_1_1/proposals").json()["items"]
    assert [i["id"] for i in items] == [second["id"], first["id"]] and "content" not in items[0]

    detail = client.get(_url(pid, second)).json()
    assert detail["content"] == "第二版" and detail["state"]["content_changed"] is False
    assert detail["state"]["current_content"] == "人工初稿" and detail["state"]["basis_changes"] == []


def test_apply_writes_once_with_version_and_is_idempotent(client, pid):
    prop = _propose(pid)
    res = client.post(_url(pid, prop, "apply"))
    assert res.status_code == 200 and res.json()["already_applied"] is False
    node = _node(pid)
    assert (node.content, node.revision, node.content_source, node.status) == ("候选稿正文", 2, "proposal", "completed")
    assert [r["ref_type"] for r in node.last_refs] == ["kb", "asset"]
    versions = _versions(client, pid)
    assert [(v["source"], v["preview"]) for v in versions] == [("proposal", "候选稿正文"), ("manual", "人工初稿")]
    assert proposal_store.get(pid, prop["id"]).status == "applied"

    again = client.post(_url(pid, prop, "apply"))
    assert again.status_code == 200 and again.json()["already_applied"] is True
    assert _node(pid).revision == 2 and len(_versions(client, pid)) == 2
    assert client.get(f"/api/v1/project/{pid}/proposals").json()["counts"] == {}


def test_apply_rejected_when_content_changed(client, pid):
    prop = _propose(pid)
    _save(client, pid, "sec_1_1", "用户等待期间手改的正文")
    assert client.get(_url(pid, prop)).json()["state"]["content_changed"] is True

    res = client.post(_url(pid, prop, "apply"))
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["reason"] == "content_changed" and "正文已变化" in detail["message"]
    assert detail["current_content"] == "用户等待期间手改的正文"
    assert _node(pid).content == "用户等待期间手改的正文"
    assert proposal_store.get(pid, prop["id"]).status == "checked"


def test_apply_rejected_when_basis_changed(client, pid):
    prop = _propose(pid)
    client.put(f"/api/v1/project/{pid}/facts", json={"company_name": "某某科技有限公司"})
    res = client.post(_url(pid, prop, "apply"))
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["reason"] == "basis_changed" and [c["label"] for c in detail["changes"]] == ["全局事实"]
    assert _node(pid).content == "人工初稿"

    # 所用资料由待核实改为已确认也算依据变化；只补传附件不算
    prop = _propose(pid)
    client.post("/api/v1/assets/personnel", json={"id": "p_li", "name": "李工", "role": "项目经理", "status": "confirmed"})
    changes = client.get(_url(pid, prop)).json()["state"]["basis_changes"]
    assert [c["label"] for c in changes] == ["企业资料：李工"]
    prop = _propose(pid)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    up = client.post("/api/v1/assets/personnel/p_li/attachments", files={"file": ("证书.png", png, "image/png")})
    assert up.status_code == 200
    assert client.post(_url(pid, prop, "apply")).status_code == 200


def test_section_settings_and_deleted_asset_change_basis(client, pid):
    prop = _propose(pid)
    client.put(f"/api/v1/project/{pid}/section/refs",
               json={"section_id": "sec_1_1", "pinned_refs": ["c9"], "excluded_refs": []})
    client.delete("/api/v1/assets/personnel/p_li")
    labels = [c["label"] for c in client.get(_url(pid, prop)).json()["state"]["basis_changes"]]
    assert labels == ["知识库引用的锁定与排除", "企业资料（已删除）：p_li"]


def test_failure_before_commit_rolls_back_content_version_and_status(client, pid, monkeypatch):
    prop = _propose(pid)

    def broken(session, proposal_id, status):
        row = session.get(SectionProposal, proposal_id)
        row.status = status
        session.add(row)
        raise RuntimeError("提交前注入的异常")

    monkeypatch.setattr(proposal_store, "set_status_in", broken)
    with pytest.raises(RuntimeError):
        prop_module.apply_proposal(pid, "sec_1_1", prop["id"])
    node = _node(pid)
    assert (node.content, node.revision) == ("人工初稿", 1)
    assert _versions(client, pid) == []
    assert proposal_store.get(pid, prop["id"]).status == "checked"


def test_reject_and_closed_proposals_cannot_be_applied(client, pid):
    prop = _propose(pid)
    assert client.post(_url(pid, prop, "reject")).json()["status"] == "rejected"
    assert client.post(_url(pid, prop, "reject")).json()["status"] == "rejected"
    res = client.post(_url(pid, prop, "apply"))
    assert res.status_code == 409 and res.json()["detail"]["reason"] == "not_open"
    assert "已放弃" in res.json()["detail"]["message"]

    applied = _propose(pid)
    client.post(_url(pid, applied, "apply"))
    res = client.post(_url(pid, applied, "reject"))
    assert res.status_code == 409 and res.json()["detail"]["reason"] == "applied"

    with get_session() as session:  # 未检查的候选稿不能采纳
        row = session.get(SectionProposal, _propose(pid, content="再改一版")["id"])
        row.status = "draft"
        session.add(row)
        draft_id = row.id
    res = client.post(f"/api/v1/project/{pid}/section/sec_1_1/proposals/{draft_id}/apply")
    assert res.status_code == 409 and "尚未完成检查" in res.json()["detail"]["message"]
    assert client.post(f"/api/v1/project/{pid}/section/sec_1_2/proposals/{draft_id}/apply").status_code == 404


def test_outline_changes_supersede_and_project_delete_removes(client, pid):
    a, b = _propose(pid), _propose(pid, sid="sec_1_2")
    stale = [{"id": "sec_1", "title": "第一章 方案", "level": 1, "children": [
        {"id": "sec_1_1", "title": "1.1 架构", "level": 2}]}]
    client.put(f"/api/v1/project/{pid}/outline", json={"outline": stale})
    assert proposal_store.get(pid, a["id"]).status == "checked"
    assert proposal_store.get(pid, b["id"]).status == "superseded"

    client.post(f"/api/v1/project/{pid}/outline/expand",
                json={"chapters": [{"title": "第一章 新方案", "requirements": []}], "total_word_budget": 1000})
    assert proposal_store.get(pid, a["id"]).status == "superseded"
    client.delete(f"/api/v1/project/{pid}")
    assert proposal_store.list(pid) == []


def _wait(client, task_id):
    for _ in range(200):
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        if task["status"] not in ("pending", "running"):
            return task
        time.sleep(0.02)
    raise AssertionError("任务超时")


def test_revise_creates_proposal_from_current_text(client, pid, monkeypatch):
    url = f"/api/v1/project/{pid}/section/sec_1_1/revise"
    res = client.post(url, json={})
    assert res.status_code == 400 and "配置大模型" in res.json()["detail"]

    monkeypatch.setattr(llm_client, "is_configured", True)
    seen = {}

    def fake_revise(evidence, base_text, issues, **kw):
        seen.update(base=base_text, issues=issues)
        return {"generated_content": "修订后的正文", "references": evidence.ref_records(), "mode": "llm"}

    monkeypatch.setattr(section_generator, "revise_section", fake_revise)
    parent = _propose(pid)
    res = client.post(url, json={"parent_id": parent["id"]})
    task = _wait(client, res.json()["task_id"])
    assert task["status"] == "completed", task
    new = proposal_store.get(pid, task["result"]["proposal_id"])
    assert (new.content, new.origin, new.parent_id, new.base_revision) == ("修订后的正文", "revise", parent["id"], 1)
    assert proposal_store.get(pid, parent["id"]).status == "superseded"
    assert seen["base"] == "人工初稿" and any("篇幅" in i for i in seen["issues"])
    assert _node(pid).content == "人工初稿"  # 只产出候选稿，不写正文

    # 模型调用失败（退回演示样例）：不产生候选稿
    monkeypatch.setattr(section_generator, "revise_section",
                        lambda **kw: {"generated_content": "演示", "references": [], "mode": "mock"})
    task = _wait(client, client.post(url, json={}).json()["task_id"])
    assert task["status"] == "failed" and len(proposal_store.list(pid, "sec_1_1", open_only=True)) == 1

    # 没有检查出问题、也没有修订要求：不修订
    res = client.post(f"/api/v1/project/{pid}/section/sec_1_2/revise", json={})
    assert res.status_code == 400 and "尚无正文" in res.json()["detail"]
    _save(client, pid, "sec_1_2", "运维服务正文。")
    res = client.post(f"/api/v1/project/{pid}/section/sec_1_2/revise", json={})
    assert res.status_code == 400 and "没有检查出问题" in res.json()["detail"]
