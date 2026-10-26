"""核实事项按业务身份合并，位置及依据失效信息不能丢失。"""
from app.models.schemas import GlobalFacts, OutlineNode
from app.services.assets.asset_manager import asset_manager
from app.services.checker.section_check import check_section
from app.services.checker.verification import project_verification, verification_counts
from app.services.checker.export_preflight import build_preflight
from app.services.project_store import project_store


def test_repeated_asset_and_placeholder_are_one_item_with_all_occurrences(project_id, monkeypatch):
    snapshot = {"personnel": [{"id": "p", "name": "王工", "status": "unverified"}]}
    monkeypatch.setattr(asset_manager, "snapshot", lambda: snapshot)
    p = project_store.get(project_id)
    p.outline = [OutlineNode(id="a", title="甲章", content="【待核实：王工】负责实施。\n【待核实：王工】负责检查。"),
                 OutlineNode(id="b", title="乙章", content="王工【待核实】负责维护。")]
    rows = project_verification(p)
    assert verification_counts(rows) == {"item_count": 1, "occurrence_count": 3}
    assert rows[0].asset_key == "personnel:p"
    assert [(o.section_id, o.line) for o in rows[0].occurrences] == [("a", 1), ("a", 2), ("b", 1)]
    assert build_preflight(p)["verification_counts"] == verification_counts(rows)


def test_scoped_equal_values_and_same_text_different_subjects_stay_separate(project_id, monkeypatch):
    monkeypatch.setattr(asset_manager, "snapshot", lambda: {"personnel": [
        {"id": "a", "name": "王工", "holder": "甲公司", "status": "unverified"},
        {"id": "b", "name": "王工", "holder": "乙公司", "status": "unverified"},
    ]})
    p = project_store.get(project_id)
    p.outline = [OutlineNode(id="s", title="服务", content="【待核实：王工】。重大故障2小时响应；一般故障2小时响应。")]
    rows = project_verification(p)
    assert len([v for v in rows if v.asset_key]) == 2
    assert len({v.fact_key for v in rows if v.fact_key}) == 2


def test_repeated_commitment_keeps_every_position(project_id):
    p = project_store.get(project_id)
    p.facts = GlobalFacts()
    n = OutlineNode(id="s", title="服务")
    r = check_section(p, n, "2小时响应。\n120分钟响应。")
    assert r.verification_count == 1 and r.verification_occurrences == 2


def test_different_generic_placeholders_and_diagram_code(project_id):
    p = project_store.get(project_id)
    n = OutlineNode(id="s", title="服务")
    r = check_section(p, n, "响应【待填写】；工期【待填写】。\n```mermaid\nA[【待填写】]\n```")
    assert r.verification_count == r.verification_occurrences == 2


def test_recorded_decision_expires_with_asset_basis(client, project_id):
    url = f"/api/v1/project/{project_id}"
    asset = {"id": "verify-person", "name": "王工", "role": "实施", "status": "unverified"}
    client.post("/api/v1/assets/personnel", json=asset)
    client.put(url + "/outline", json={"outline": [{"id": "s", "title": "团队", "content": "【待核实：王工】"}]})
    item = client.get(url + "/verification").json()["items"][0]
    body = {"basis_hash": item["basis_hash"], "note": "已电话核对，待补证书"}
    endpoint = url + "/verification/" + item["stable_key"]
    assert client.put(endpoint, json=body).status_code == 200
    assert client.get(url + "/verification").json()["items"][0]["decision_state"] == "confirmed"
    client.post("/api/v1/assets/personnel", json={**asset, "role": "项目经理"})
    changed = client.get(url + "/verification").json()["items"][0]
    assert changed["decision_state"] == "stale" and changed["stable_key"] == item["stable_key"]
    assert client.put(endpoint, json=body).status_code == 409
    assert project_store.get(project_id).verification_decisions[item["stable_key"]]["note"] == body["note"]
