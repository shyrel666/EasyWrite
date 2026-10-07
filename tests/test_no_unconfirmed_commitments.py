"""阶段 A：未经用户确认，系统不自行产生企业承诺（提示词、离线演示文本、润色规则）"""
import re

from app.core.llm_client import llm_client
from app.models.schemas import GlobalFacts, PolishSectionRequest
from app.services.checker.quality_inspector import quality_inspector
from app.services.generator.section_generator import _select_system_prompt

# 具体承诺数值：7×24、N 分钟/小时/日历天内……
COMMITMENT_NUMBER = re.compile(r"7\s*[×xX*]\s*24|\d+\s*(?:分钟|小时|日历天|工作日|天)")


def test_specialized_prompts_have_no_fixed_commitments():
    for title in ["第五章 售后运维保障方案", "3.1 项目团队人员配置", "2.1 总体架构设计", "4.2 数据迁移方案"]:
        prompt = _select_system_prompt(title)
        assert not COMMITMENT_NUMBER.search(prompt), title
        assert "承诺纪律" in prompt
    assert "以全局事实为准" in _select_system_prompt("第五章 售后运维保障方案")


def test_offline_demo_uses_placeholders_instead_of_commitments():
    text = llm_client._mock_bid_generation("", "投标人官方全称：某某科技有限公司")
    assert not COMMITMENT_NUMBER.search(text)
    assert "TPS" not in text
    assert "【待填写】" in text


def test_rule_polish_does_not_turn_hedges_into_commitments():
    """口语/模糊表述只提示不改写：不能把"基本上满足"改成"我方承诺" """
    req = PolishSectionRequest(project_id="p", section_id="s", polish_mode="de_ai",
                               content="众所周知，该接口基本上满足招标要求，可能需要二次开发。")
    resp = quality_inspector.polish_section(req, GlobalFacts())
    assert resp.mode == "rules"
    assert "我方承诺" not in resp.polished_content
    assert "基本上满足" in resp.polished_content and "众所周知" not in resp.polished_content
    assert any("基本上满足" in note for note in resp.improvements)
