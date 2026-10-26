"""分级企业承诺的作用域、确认边界与采购要求隔离。"""
import pytest

from app.models.schemas import Commitment, GlobalFacts, OutlineNode
from app.services.checker import section_check as sc
from app.services.checker.book_consistency import book_consistency
from app.services.project_store import project_store


def facts(**extra):
    return GlobalFacts(commitments=[
        Commitment(topic="response", value="2", severity="重大故障", confirmed=True, **extra),
        Commitment(topic="response", value="24", severity="一般故障", confirmed=True, **extra),
    ])


@pytest.mark.parametrize("text,blocking,pending", [
    ("重大故障2小时响应；一般故障24小时响应。", 0, 0),
    ("重大故障3小时响应；一般故障24小时响应。", 1, 0),
    ("重大故障120分钟响应；一般故障1天响应。", 0, 0),
    ("重大故障1小时响应。", 0, 1),
    ("2小时响应。", 0, 1),
])
def test_scope_and_units(project_id, text, blocking, pending):
    p = project_store.get(project_id)
    p.facts = facts()
    p.outline = [OutlineNode(id="s1", title="服务", content=text)]
    issues, verify = sc._check_commitments(text, p.facts)
    book = book_consistency(p)
    assert len(issues) == len(book["fact_conflicts"]) == blocking
    assert len(verify) == book["commitments_unbacked"] == pending


def test_unqualified_legacy_fact_does_not_authorize_tiers():
    issues, verify = sc._check_commitments("重大故障24小时响应", GlobalFacts(sla_commitment="2小时响应"))
    assert not issues and "不能映射" in verify[0].note
    assert sc._check_commitments("2小时响应", GlobalFacts(sla_commitment="2小时响应")) == ([], [])
    legacy = GlobalFacts(sla_commitment="重大故障2小时响应；一般故障24小时响应")
    assert sc._check_commitments("重大故障2小时响应；一般故障24小时响应", legacy) == ([], [])


def test_subject_and_scene_must_be_explicit():
    f = facts(subject="甲公司", scope="生产环境")
    assert sc._check_commitments("甲公司在生产环境重大故障2小时响应", f) == ([], [])
    assert sc._check_commitments("重大故障2小时响应", f)[1]
    assert sc._check_commitments("乙公司在测试环境重大故障2小时响应", f)[1]


def test_unconfirmed_never_enters_prompt_or_authorizes_text():
    f = GlobalFacts(commitments=[Commitment(topic="response", value="6", confirmed=False)])
    assert "6小时" not in f.to_constraint_text()
    assert sc._check_commitments("6小时响应", f)[1]


def test_cross_chapter_tiers_are_separate(project_id):
    p = project_store.get(project_id)
    p.outline = [OutlineNode(id="a", title="甲", content="重大故障2小时响应"),
                 OutlineNode(id="b", title="乙", content="一般故障24小时响应")]
    assert book_consistency(p)["value_conflicts"] == []


def test_requirement_apply_and_confirmation_roundtrip(client, project_id):
    url = f"/api/v1/project/{project_id}"
    r = client.post(url + "/tender/apply", json={"analysis": {
        "duration_requirement": "工期30天", "star_disqualification_items": ["重大故障2小时响应"],
    }})
    assert r.status_code == 200
    p = client.get(url).json()
    assert p["facts"]["commitments"] == []
    required = p["tender_analysis"]["commitment_requirements"]
    assert len(required) == 2 and all(not c["confirmed"] for c in required)
    required[1]["confirmed"] = True
    payload = {**p["facts"], "commitments": [required[1]]}
    assert client.put(url + "/facts", json=payload).status_code == 200
    saved = client.get(url).json()
    assert saved["facts"]["commitments"] == [required[1]]
    assert not saved["tender_analysis"]["commitment_requirements"][1]["confirmed"]
    assert "重大故障" in GlobalFacts(**saved["facts"]).to_constraint_text()


def test_opposite_comparators_conflict():
    f = GlobalFacts(commitments=[Commitment(topic="response", value="2", comparator="le", confirmed=True)])
    assert sc._check_commitments("响应不少于2小时", f)[0]


def test_structured_inspection_and_service_window():
    f = GlobalFacts(commitments=[Commitment(topic="inspection", value="1", unit="次/月", confirmed=True),
                                Commitment(topic="window", value="7×24", unit="小时", confirmed=True)])
    assert sc._check_commitments("每月巡检1次；提供7×24小时服务", f) == ([], [])
