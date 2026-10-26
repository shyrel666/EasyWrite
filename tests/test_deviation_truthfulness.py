"""偏离表承诺不得因缺依据、格式降级或红线要求而被改成满足。"""
import pytest

from app.core.llm_client import llm_client
from app.models.schemas import DeviationItem, GlobalFacts
from app.services.checker.export_preflight import build_preflight
from app.services.parser.deviation_engine import DEVIATION_PROMPT, deviation_engine


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("status,expected", [("负偏离", "负偏离"), ("待核实", "待核实"), ("未知", "待核实"), (None, "待核实")])
def test_response_status_and_complete_fact_constraints(monkeypatch, batch, status, expected):
    facts = GlobalFacts(sla_commitment="4小时响应", delivery_guarantee="60天交付", custom_facts={"驻场": "2人"})
    item = DeviationItem(index=1, clause_title="须1小时响应", level="redline")

    def answer(**kwargs):
        assert "4小时响应" in kwargs["user_prompt"]
        assert "60天交付" in kwargs["user_prompt"] and "2人" in kwargs["user_prompt"]
        assert "绝不可" not in kwargs["user_prompt"]
        response = {"response_detail": "已确认能力为4小时响应，请核实与招标要求的差距。"}
        if status is not None:
            response["response_status"] = status
        return {"responses": [{"no": 1, **response}]} if batch else response

    monkeypatch.setattr(llm_client, "is_configured", True)
    monkeypatch.setattr(llm_client, "get_mode", lambda: "llm")
    monkeypatch.setattr(llm_client, "chat_completion_structured", answer)
    monkeypatch.setattr(deviation_engine, "_item_context", lambda *a, **k: ("", ""))
    if batch:
        deviation_engine.batch_generate_responses([item], facts)
    else:
        deviation_engine.generate_response_for_item(item, facts)
    assert item.response_status == expected
    assert item.response_source == "ai"


def test_plain_text_does_not_guess_positive_status(monkeypatch):
    monkeypatch.setattr(llm_client, "is_configured", True)
    monkeypatch.setattr(llm_client, "get_mode", lambda: "llm")
    monkeypatch.setattr(llm_client, "chat_completion_structured", lambda **k: None)
    monkeypatch.setattr(llm_client, "chat_completion", lambda **k: "尚无依据确认优于招标要求，请补齐材料。")
    monkeypatch.setattr(deviation_engine, "_item_context", lambda *a, **k: ("", ""))
    item = DeviationItem(index=1, clause_title="提供资质")
    deviation_engine.generate_response_for_item(item, GlobalFacts())
    assert item.response_status == "待核实"
    assert '无相关能力依据' in DEVIATION_PROMPT


def test_unverified_response_warns_in_export_preflight_and_markdown(project_id):
    from app.services.project_store import project_store
    item = DeviationItem(index=1, clause_title="须提供证明", response_status="待核实")
    text = deviation_engine.to_markdown_table([item])
    assert "不存在负偏离" not in text
    assert "待核实" in text
    project = project_store.get(project_id)
    project.deviation_matrix = [item]
    report = build_preflight(project)
    check = next(c for c in report["checks"] if c["key"] == "deviation_pending")
    assert check["status"] == "warn"
