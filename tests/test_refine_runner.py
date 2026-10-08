"""阶段 D1/D3：智能完善（单章节写—查—改闭环）后台任务"""
import pytest
from sqlmodel import select

from app.core.config import settings
from app.core.llm_client import llm_client
from app.db.database import get_session
from app.db.models import LLMCallLog
from app.services.proposals import proposal_store
from app.services.refine import runner
from refine_stub import FULL, HALF, LACKING, OUTLINE, FakeModel, chunk, no_kb, node, refine


def _pinned_select(pins):
    original = runner.select_evidence

    def select(project, node, **kw):
        kw["pinned_refs"] = pins
        return original(project, node, **kw)
    return select


def _with_kb_ref(monkeypatch):
    """本节锁定一条知识库参考（不触发重排）：有资料时停止原因是"没有进展"而不是"资料不足"。"""
    no_kb(monkeypatch, chunks={"c1": chunk("c1")})
    monkeypatch.setattr(runner, "select_evidence", _pinned_select(["c1"]))


@pytest.fixture()
def pid(client, project_id, monkeypatch):
    client.put(f"/api/v1/project/{project_id}/outline", json={"outline": OUTLINE})
    no_kb(monkeypatch)
    return project_id


def _calls(task_id):
    with get_session() as session:
        return [(r.purpose, r.run_id) for r in session.exec(
            select(LLMCallLog).where(LLMCallLog.run_id == task_id).order_by(LLMCallLog.id)).all()]


def test_blank_section_draft_check_revise_until_goal(client, pid, monkeypatch):
    model = FakeModel(monkeypatch, drafts=[LACKING], revisions=[FULL])
    task = refine(client, pid)
    assert task["status"] == "completed", task
    assert task["meta"] == {"section_id": "sec_1_1", "outcome": "goal_met"}
    r = task["result"]
    assert (r["outcome"], r["stop_reason"], r["base"], r["rounds"], r["max_rounds"]) == ("goal_met", "goal_met", "draft", 1, 2)
    assert [(h["label"], h["blocking_count"]) for h in r["history"]] == [("起草稿", 2), ("第 1 轮修订", 0)]
    assert model.kinds() == ["draft", "revise"]
    # 修订的输入：上一版、问题清单（逐条，附定位）
    revise_prompt = model.user_prompts("revise")[0]
    assert LACKING in revise_prompt and "故障分级响应" in revise_prompt and "季度巡检安排" in revise_prompt

    draft_id, final_id = r["proposal_ids"]
    final, draft = proposal_store.get(pid, final_id), proposal_store.get(pid, draft_id)
    assert (final.content, final.origin, final.parent_id, final.status) == (FULL, "refine", draft_id, "checked")
    assert (draft.origin, draft.status) == ("refine_draft", "superseded")
    assert r["final_proposal_id"] == final_id and r["applied"] is False
    assert node(pid).content == ""  # 只产出候选稿，不写正文

    # 调用记录按运行归集：起草与修订各一次，运行预算如实计数
    assert _calls(task["id"]) == [("section_refine_draft", task["id"]), ("section_revise", task["id"])]
    assert r["budget"]["calls"] == 2 and r["usage"]["calls"] == 2 and r["usage"]["total_tokens"] == 300
    by_run = client.get(f"/api/v1/ai/usage?days=1&run_id={task['id']}").json()
    assert by_run["totals"]["calls"] == 2 and by_run["by_run"][0]["type"] == "section_refine"


def test_blank_section_written_directly_through_adoption_when_goal_met(client, pid, monkeypatch):
    # 未达成目标时不直接写入
    FakeModel(monkeypatch, drafts=[LACKING])
    r = refine(client, pid, apply_if_blank=True, max_rounds=0)["result"]
    assert (r["outcome"], r["stop_reason"], r["applied"]) == ("partial", "insufficient_evidence", False)
    assert node(pid).content == ""

    FakeModel(monkeypatch, drafts=[FULL])
    r = refine(client, pid, apply_if_blank=True)["result"]
    assert (r["outcome"], r["rounds"], r["applied"]) == ("goal_met", 0, True)
    n = node(pid)
    assert (n.content, n.content_source, n.revision) == (FULL, "proposal", 1)
    assert proposal_store.get(pid, r["final_proposal_id"]).status == "applied"


def test_current_text_is_the_base_and_instruction_forces_one_round(client, pid, monkeypatch):
    client.put(f"/api/v1/project/{pid}/section", json={"section_id": "sec_1_1", "content": FULL})
    model = FakeModel(monkeypatch, revisions=[FULL + "运维组按月汇总工单。"])
    res = refine(client, pid, expect=400)
    assert "已通过检查" in res["detail"]

    r = refine(client, pid, instruction="补充工单汇总的做法")["result"]
    assert (r["base"], r["outcome"], r["rounds"]) == ("current", "goal_met", 1)
    assert [h["label"] for h in r["history"]] == ["当前正文", "第 1 轮修订"]
    assert model.kinds() == ["revise"]  # 以当前正文为原稿，不起草
    prompt = model.user_prompts("revise")[0]
    assert FULL in prompt and "补充修订要求：补充工单汇总的做法" in prompt
    prop = proposal_store.get(pid, r["final_proposal_id"])
    assert prop.base_revision == node(pid).revision and node(pid).content == FULL


def test_no_progress_switches_method_then_stops(client, pid, monkeypatch):
    _with_kb_ref(monkeypatch)
    model = FakeModel(monkeypatch, drafts=[LACKING], revisions=[LACKING])
    r = refine(client, pid)["result"]
    assert (r["outcome"], r["stop_reason"], r["rounds"]) == ("partial", "no_improvement", 2)
    assert [h["mode"] for h in r["history"]] == ["draft", "normal", "alternate"]
    prompts = model.user_prompts("revise")
    assert "换一种修改方式" not in prompts[0] and "换一种修改方式" in prompts[1]
    assert r["blocking_count"] == 2 and {i["code"] for i in r["unresolved"]} == {"missing_point"}
    assert proposal_store.get(pid, r["final_proposal_id"]).status == "checked"  # 最后一版候选稿保留


def test_minimal_mode_and_insufficient_evidence(client, pid, monkeypatch):
    model = FakeModel(monkeypatch, drafts=[LACKING], revisions=[LACKING])
    r = refine(client, pid, max_rounds=3)["result"]
    assert [h["mode"] for h in r["history"]] == ["draft", "normal", "alternate", "minimal"]
    assert "只处理上面列出的阻塞问题" in model.user_prompts("revise")[2]
    # 要点没写到、知识库与企业资料都没有可用依据：如实报告资料不足
    assert (r["outcome"], r["stop_reason"]) == ("partial", "insufficient_evidence")
    assert "知识库中没有本节的高置信参考" in r["message"] and "知识库中没有本节的高置信参考" in r["evidence_gaps"]


def test_budget_exhausted_keeps_proposals_and_resume_does_not_regain_rounds(client, pid, monkeypatch):
    _with_kb_ref(monkeypatch)
    monkeypatch.setattr(settings, "REFINE_MAX_CALLS", 2)
    FakeModel(monkeypatch, drafts=[LACKING], revisions=[HALF, FULL])
    task = refine(client, pid)
    r = task["result"]
    assert task["status"] == "completed" and task["meta"]["outcome"] == "partial"
    assert (r["outcome"], r["stop_reason"], r["rounds"]) == ("partial", "budget_exhausted", 1)
    assert r["budget"]["exceeded"] == "calls" and "继续" in r["message"]
    tip = proposal_store.get(pid, r["final_proposal_id"])
    assert (tip.content, tip.status) == (HALF, "checked")

    # 继续：从最后一版接着修订；已用 1 轮，原上限 2 轮，请求中的 3 轮不会生效
    monkeypatch.setattr(settings, "REFINE_MAX_CALLS", 15)
    model = FakeModel(monkeypatch, revisions=[HALF])
    r2 = refine(client, pid, resume=True, max_rounds=3)["result"]
    assert (r2["base"], r2["rounds"], r2["max_rounds"]) == ("resume", 2, 2)
    assert model.kinds() == ["revise"] and HALF in model.user_prompts("revise")[0]
    assert [h["label"] for h in r2["history"]] == ["起草稿", "第 1 轮修订", "第 2 轮修订"]
    new = proposal_store.get(pid, r2["final_proposal_id"])
    assert new.parent_id == tip.id and new.base_revision == tip.base_revision
    assert (r2["outcome"], r2["stop_reason"]) == ("partial", "no_improvement")

    # 轮数已用完：再继续也不会多修订
    model = FakeModel(monkeypatch, revisions=[FULL])
    r3 = refine(client, pid, resume=True)["result"]
    assert model.kinds() == [] and r3["rounds"] == 2 and r3["final_proposal_id"] == new.id


def test_validation_and_failures(client, pid, monkeypatch):
    res = refine(client, pid, expect=400)
    assert "配置大模型" in res["detail"]
    FakeModel(monkeypatch, drafts=[RuntimeError("服务不可用")])
    assert "0–3" in refine(client, pid, expect=400, max_rounds=4)["detail"]
    assert "没有可继续" in refine(client, pid, expect=400, resume=True)["detail"]
    assert runner.claim(pid, "sec_1_1")
    try:
        assert "已有智能完善" in refine(client, pid, expect=409)["detail"]
    finally:
        runner.release(pid, "sec_1_1")

    # 模型调用失败退回演示样例：不产生候选稿，任务失败
    task = refine(client, pid)
    assert task["status"] == "failed" and "未能起草" in task["error"]
    assert proposal_store.list(pid, "sec_1_1") == []
    assert runner.claim(pid, "sec_1_1")  # 结束后释放
    runner.release(pid, "sec_1_1")


def test_cancel_and_error_after_output_keep_last_proposal(client, pid, monkeypatch):
    class Ctx:
        task_id = "task_refine_cancel"

        def __init__(self, cancel_after):
            self.cancel_after = cancel_after

        def cancelled(self):
            return len(proposal_store.list(pid, "sec_1_1")) >= self.cancel_after

        def report(self, *a):
            pass

    FakeModel(monkeypatch, drafts=[LACKING], revisions=[HALF])
    r = runner.run_refine(Ctx(1), pid, "sec_1_1")
    assert (r["outcome"], r["stop_reason"], r["rounds"]) == ("partial", "cancelled", 0)

    FakeModel(monkeypatch, drafts=[LACKING], revisions=[HALF, RuntimeError("连接中断")])
    monkeypatch.setattr(llm_client, "_mock_bid_generation", lambda *a: "")
    r = runner.run_refine(Ctx(99), pid, "sec_1_1")
    assert (r["outcome"], r["stop_reason"]) == ("partial", "error")
    assert proposal_store.get(pid, r["final_proposal_id"]).content == HALF


def test_interrupted_task_can_resume_from_chain(client, pid, monkeypatch):
    _with_kb_ref(monkeypatch)
    FakeModel(monkeypatch, drafts=[LACKING], revisions=[HALF])
    first = refine(client, pid, max_rounds=1)["result"]
    assert (first["rounds"], first["stop_reason"]) == (1, "rounds_exhausted")
    # 轮数已用完的链继续执行也不会获得新的修订额度
    model = FakeModel(monkeypatch, revisions=[FULL])
    again = refine(client, pid, resume=True)["result"]
    assert model.kinds() == [] and again["rounds"] == 1
    # 采纳后不能再继续
    client.post(f"/api/v1/project/{pid}/section/sec_1_1/proposals/{first['final_proposal_id']}/apply")
    assert "没有可继续" in refine(client, pid, expect=400, resume=True)["detail"]
