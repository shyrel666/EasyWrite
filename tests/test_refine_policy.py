"""阶段 D2：智能完善的提交策略（纯函数）"""
import pytest

from app.models.schemas import SectionCheckReport
from app.services.refine.policy import decide, progressed, stalls


def r(blocking, quality=0):
    return {"blocking_count": blocking, "quality_count": quality}


def test_goal_met_when_no_blocking_and_quality_within_threshold():
    action = decide([r(0, 2)], rounds_used=0, max_rounds=2)
    assert (action.kind, action.outcome, action.stop_reason) == ("accept", "goal_met", "goal_met")
    assert decide([r(0, 3)], 0, 2).kind == "revise"
    assert decide([r(0, 3)], 0, 2, quality_threshold=3).kind == "accept"
    # 检查报告对象同样可用
    assert decide([SectionCheckReport(section_id="s", blocking_count=0, quality_count=0)], 0, 2).outcome == "goal_met"


def test_progress_definition():
    assert progressed(r(3, 0), r(2, 5))          # 阻塞问题减少
    assert not progressed(r(2, 5), r(2, 1))      # 阻塞未清零时，质量问题减少不算进展
    assert progressed(r(0, 5), r(0, 3))          # 阻塞已清零：质量问题减少
    assert not progressed(r(0, 3), r(0, 3))
    assert not progressed(r(1, 3), r(2, 0))
    assert stalls([r(3), r(3), r(2), r(2), r(2)]) == (2, 2)
    assert stalls([r(3), r(3), r(3), r(2)]) == (0, 2)


def test_alternate_then_minimal_then_stop():
    reports = [r(3, 4)]
    assert decide(reports, 0, 3).mode == "normal"
    reports.append(r(3, 4))                       # 第 1 轮无进展 → 换方法
    assert (decide(reports, 1, 3).kind, decide(reports, 1, 3).mode) == ("revise", "alternate")
    reports.append(r(3, 1))                       # 第 2 轮仍无进展 → 最低目标模式
    assert decide(reports, 2, 3).mode == "minimal"
    reports.append(r(3, 1))                       # 第 3 轮仍无进展，轮数用尽 → 停止
    action = decide(reports, 3, 3)
    assert (action.kind, action.outcome, action.stop_reason) == ("stop", "partial", "no_improvement")


def test_minimal_mode_is_sticky_and_accepts_once_blocking_cleared():
    reports = [r(3, 4), r(3, 4), r(3, 4), r(1, 6)]   # 两轮无进展后，第 3 轮阻塞减少
    assert decide(reports, 3, 4).mode == "minimal"    # 有进展也不回到普通模式
    reports.append(r(0, 6))
    action = decide(reports, 4, 4)
    assert (action.kind, action.outcome, action.stop_reason) == ("accept", "partial", "no_improvement")


def test_rounds_exhausted_after_progress():
    action = decide([r(3), r(1, 5)], rounds_used=1, max_rounds=1)
    assert (action.kind, action.stop_reason, action.outcome) == ("stop", "rounds_exhausted", "partial")
    assert "1 个阻塞问题" in action.message
    assert decide([r(2)], 0, 0).stop_reason == "rounds_exhausted"   # 0 轮：只起草和检查


def test_default_two_rounds_without_progress_stops_with_no_improvement():
    reports = [r(2), r(2)]
    assert decide(reports, 1, 2).mode == "alternate"
    action = decide(reports + [r(2)], 2, 2)
    assert (action.kind, action.stop_reason) == ("stop", "no_improvement")


def test_requires_a_report():
    with pytest.raises(ValueError):
        decide([], 0, 2)
