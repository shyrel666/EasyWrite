"""
阶段 D 验收（可控的模型桩替换 OpenAI 客户端，预算检查与调用记录照常经过 llm_client）：
- 闭环可完整走通：起草 → 检查 → 修订 → 候选稿
- 修订后阻塞问题不减少时，按策略依次换方法、降级、停止
- 检索重排、正文生成、截断重试都能触发预算上限；触发后，已有候选稿可以查到
- 资料不足时输出待核实项，正文不出现示例资料中的证书编号或业绩名称
- 中断后继续执行，不会重新获得修订轮数
"""
import time

import pytest
from sqlmodel import select

from app.core.config import settings
from app.core.task_manager import task_manager
from app.db.database import get_session
from app.db.models import LLMCallLog
from app.services.assets.asset_manager import asset_manager
from app.services.proposals import proposal_store
from refine_stub import FULL, HALF, LACKING, OUTLINE, FakeModel, chunk, no_kb, node, refine, wait

TEAM_OUTLINE = [{"id": "sec_2", "title": "第二章 项目团队", "level": 1, "children": [
    {"id": "sec_2_1", "title": "2.1 项目团队与人员资质", "level": 2, "requirements": ["评分子项：项目经理资质（4分）"]},
]}]


class Crash(BaseException):
    """模拟服务进程在任务中途退出（不被任务管理器当作普通失败处理）"""


@pytest.fixture()
def pid(client, project_id, monkeypatch):
    client.put(f"/api/v1/project/{project_id}/outline", json={"outline": OUTLINE + TEAM_OUTLINE})
    no_kb(monkeypatch)
    return project_id


def _calls(task_id):
    with get_session() as session:
        return [(r.purpose, r.status) for r in session.exec(
            select(LLMCallLog).where(LLMCallLog.run_id == task_id).order_by(LLMCallLog.id)).all()]


def test_full_loop_draft_check_revise_proposal(client, pid, monkeypatch):
    model = FakeModel(monkeypatch, drafts=[LACKING], revisions=[HALF, FULL])
    task = refine(client, pid)
    r = task["result"]
    assert (r["outcome"], r["stop_reason"], r["rounds"]) == ("goal_met", "goal_met", 2)
    assert [(h["label"], h["blocking_count"]) for h in r["history"]] == [("起草稿", 2), ("第 1 轮修订", 1), ("第 2 轮修订", 0)]
    assert model.kinds() == ["draft", "revise", "revise"]
    final = client.get(f"/api/v1/project/{pid}/section/sec_1_1/proposals/{r['final_proposal_id']}").json()
    assert final["content"] == FULL and final["status"] == "checked" and final["report"]["blocking_count"] == 0
    assert final["refine"]["round"] == 2 and final["origin"] == "refine"
    assert node(pid).content == ""  # 只产出候选稿
    assert [p for p, _ in _calls(task["id"])] == ["section_refine_draft", "section_revise", "section_revise"]


def test_no_progress_changes_method_then_degrades_then_stops(client, pid, monkeypatch):
    model = FakeModel(monkeypatch, drafts=[LACKING], revisions=[LACKING])
    r = refine(client, pid, max_rounds=3)["result"]
    assert [h["mode"] for h in r["history"]] == ["draft", "normal", "alternate", "minimal"]
    prompts = model.user_prompts("revise")
    assert "换一种修改方式" in prompts[1] and "只处理上面列出的阻塞问题" in prompts[2]
    assert r["outcome"] == "partial" and r["rounds"] == 3
    assert r["stop_reason"] in ("no_improvement", "insufficient_evidence")
    assert proposal_store.get(pid, r["final_proposal_id"]).status == "checked"


def test_budget_hits_rerank_generation_and_truncation_retry(client, pid, monkeypatch):
    # 正文生成：重排 1 次 + 起草 1 次后，修订被拦下；起草稿可以查到
    no_kb(monkeypatch, chunks={"c1": chunk("c1")}, recall=True)
    monkeypatch.setattr(settings, "REFINE_MAX_CALLS", 2)
    FakeModel(monkeypatch, drafts=[LACKING], revisions=[FULL])
    task = refine(client, pid)
    r = task["result"]
    assert (r["outcome"], r["stop_reason"], r["budget"]["by_purpose"]) == (
        "partial", "budget_exhausted", {"rerank": 1, "section_refine_draft": 1})
    draft_id = r["final_proposal_id"]
    assert proposal_store.get(pid, draft_id).status == "checked"

    # 检索重排：继续执行时第一个请求（重排）就超限；已有候选稿仍可查到
    monkeypatch.setattr(settings, "REFINE_MAX_CALLS", 0)
    r = refine(client, pid, resume=True)["result"]
    assert (r["stop_reason"], r["final_proposal_id"], r["budget"]["calls"]) == ("budget_exhausted", draft_id, 0)
    assert client.get(f"/api/v1/project/{pid}/section/sec_1_1/proposals/{draft_id}").json()["status"] == "checked"

    # 截断重试：修订被截断后放大额度重试时超限；被拦下的重试不发请求
    no_kb(monkeypatch)
    monkeypatch.setattr(settings, "REFINE_MAX_CALLS", 1)
    FakeModel(monkeypatch, revisions=[("", "length"), FULL])
    task = refine(client, pid, resume=True)
    r = task["result"]
    assert r["stop_reason"] == "budget_exhausted" and r["final_proposal_id"] == draft_id
    assert _calls(task["id"]) == [("section_revise", "truncated")]


def test_insufficient_evidence_marks_placeholders_and_never_uses_examples(client, pid, monkeypatch):
    example_person = next(p for p in asset_manager.personnel if p.get("status") == "example")
    example_qual = next(q for q in asset_manager.qualifications if q.get("status") == "example" and q.get("cert_no"))
    example_case = next(c for c in asset_manager.cases if c.get("status") == "example")
    examples = [example_person["name"], example_qual["cert_no"], example_case["project_name"]]
    # 模型桩起草时"编造"了示例资料：检查判为阻塞，修订后改为【待核实】占位
    hallucinated = (f"项目经理资质：由{example_person['name']}担任项目经理，持有证书 {example_qual['cert_no']}，"
                    f"曾负责{example_case['project_name']}。")
    honest = "项目经理资质：项目经理人选与证书【待核实：项目经理姓名、证书编号】，类似业绩【待填写】。"
    model = FakeModel(monkeypatch, drafts=[hallucinated], revisions=[honest])
    r = refine(client, pid, sid="sec_2_1")["result"]

    draft_prompt = model.user_prompts("draft")[0]
    assert "尚未录入" in draft_prompt and not any(x in draft_prompt for x in examples)  # 提示词里没有示例资料
    assert "示例资料" in model.user_prompts("revise")[0]
    assert [h["blocking_count"] for h in r["history"]][0] >= 1
    final = client.get(f"/api/v1/project/{pid}/section/sec_2_1/proposals/{r['final_proposal_id']}").json()
    assert final["content"] == honest and not any(x in final["content"] for x in examples)
    assert final["report"]["blocking_count"] == 0
    assert {v["kind"] for v in final["report"]["pending_verification"]} == {"placeholder"}
    assert any("尚未录入" in g for g in r["evidence_gaps"])
    assert r["outcome"] == "goal_met"


def test_interrupted_run_resumes_without_regaining_rounds(client, pid, monkeypatch):
    FakeModel(monkeypatch, drafts=[LACKING], revisions=[HALF, Crash()])
    res = client.post(f"/api/v1/project/{pid}/section/sec_1_1/refine", json={"max_rounds": 2})
    task_id = res.json()["task_id"]
    for _ in range(500):  # 等工作线程退出（任务记录停在"进行中"，与服务进程中途退出一样）
        if task_id not in task_manager._tasks:
            break
        time.sleep(0.02)
    task_manager.recover_interrupted()  # 服务重启时的处理
    task = wait(client, task_id)
    assert task["status"] == "interrupted"
    tip = proposal_store.list(pid, "sec_1_1", open_only=True)[0]
    assert (tip.content, tip.origin) == (HALF, "refine")

    # 继续：已用 1 轮，原上限 2 轮 → 只再修订 1 轮
    model = FakeModel(monkeypatch, revisions=[FULL])
    r = refine(client, pid, resume=True, max_rounds=3)["result"]
    assert model.kinds() == ["revise"] and r["rounds"] == 2 and r["max_rounds"] == 2
    assert [h["label"] for h in r["history"]] == ["起草稿", "第 1 轮修订", "第 2 轮修订"]
    assert r["outcome"] == "goal_met"
    assert proposal_store.get(pid, r["final_proposal_id"]).parent_id == tip.id

    # 再继续也不会多修订
    model = FakeModel(monkeypatch, revisions=[HALF])
    again = refine(client, pid, resume=True)["result"]
    assert model.kinds() == [] and again["rounds"] == 2 and again["outcome"] == "goal_met"
