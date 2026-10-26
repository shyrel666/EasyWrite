"""连续压缩、保留评分点及独立图表预算的离线模型回归。"""
import pytest

from app.models.schemas import OutlineNode
from app.services.checker.section_check import check_section
from app.services.project_store import project_store
from app.services.proposals import proposal_store
from app.services.refine.policy import progressed
from app.services.text_stats import body_chars
from refine_stub import FULL, OUTLINE, FakeModel, no_kb, refine


def sized(size):
    return FULL + "内容" * max(0, (size - body_chars(FULL)) // 2)


@pytest.fixture()
def pid(client, project_id, monkeypatch):
    outline = [{**OUTLINE[0], "children": [{**OUTLINE[0]["children"][0], "word_budget": 100}]}]
    client.put(f"/api/v1/project/{project_id}/outline", json={"outline": outline})
    no_kb(monkeypatch)
    return project_id


def test_continuous_length_progress_reaches_goal(client, pid, monkeypatch):
    model = FakeModel(monkeypatch, drafts=[sized(180)], revisions=[sized(155), sized(135), sized(110)])
    r = refine(client, pid, max_rounds=3)["result"]
    assert r["outcome"] == "goal_met" and r["rounds"] == 3
    assert [h["mode"] for h in r["history"]] == ["draft", "compress", "compress", "compress"]
    assert r["length"]["char_count"] <= 110
    assert all("压缩计划" in p and "故障分级响应" in p for p in model.user_prompts("revise"))


def test_stalled_compression_stops_partial_without_truncation(client, pid, monkeypatch):
    original = sized(180)
    FakeModel(monkeypatch, drafts=[original], revisions=[original])
    r = refine(client, pid, max_rounds=3)["result"]
    assert r["outcome"] == "partial" and r["stop_reason"] == "no_improvement"
    assert r["rounds"] == 2
    assert proposal_store.get(pid, r["final_proposal_id"]).content == original


def test_removing_scoring_point_rolls_back_and_keeps_rejected_candidate(client, pid, monkeypatch):
    original = sized(180)
    FakeModel(monkeypatch, drafts=[original], revisions=["执行作业流程。" * 14])
    r = refine(client, pid, max_rounds=1)["result"]
    assert r["outcome"] == "partial" and r["blocking_count"] == 0
    assert proposal_store.get(pid, r["final_proposal_id"]).content == original
    rejected = next(h for h in r["history"] if h.get("reverted"))
    assert proposal_store.get(pid, rejected["proposal_id"]).status == "rejected"


@pytest.mark.parametrize("budget,text", [(1, FULL), (100, "```mermaid\ngraph TD\nA-->B\n```")])
def test_tiny_budget_and_diagram_only_remain_partial(client, pid, monkeypatch, budget, text):
    project_store.update(pid, lambda p: setattr(p.outline[0].children[0], "word_budget", budget))
    FakeModel(monkeypatch, drafts=[text], revisions=[text])
    r = refine(client, pid)["result"]
    assert r["outcome"] == "partial"
    assert proposal_store.get(pid, r["final_proposal_id"]).content == text


def test_body_and_diagram_metrics_are_independent(project_id):
    p = project_store.get(project_id)
    n = OutlineNode(id="s", title="方案", word_budget=10, diagram_budget=10)
    r = check_section(p, n, "文" * 10 + "\n```mermaid\ngraph TD\nA-->B\n```")
    assert r.char_count == 10 and r.length_deviation == 0 and r.excess_chars == 0
    assert r.diagram_chars > 10 and any(i.code == "diagram_length" for i in r.issues)
    assert progressed({"blocking_count": 0, "quality_count": 1, "char_count": 180, "word_budget": 100},
                      {"blocking_count": 0, "quality_count": 1, "char_count": 135, "word_budget": 100})
