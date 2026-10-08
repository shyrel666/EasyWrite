"""
智能完善的提交策略（参考易标 piSubmissionPolicy）：纯函数，只看各版的检查结果决定下一步，不读写任何状态。

reports 按时间顺序排列：第一项是原稿（起草稿或当前正文）的检查结果，之后每轮修订一项；
每项只用到阻塞问题数 blocking_count 与质量问题数 quality_count（SectionCheckReport、候选稿摘要或同名键的字典均可）。

- 没有阻塞问题且质量问题不超过阈值：接受，目标达成
- "有进展"：阻塞问题数下降；或阻塞问题已清零时，质量问题数下降
- 连续 1 轮无进展：下一轮换一种修改方式（alternate）
- 连续 2 轮无进展：进入最低目标模式（minimal，此后一直保持）——只处理阻塞问题，阻塞清零即接受，结果为部分完成
- 轮数用尽：停止，输出最后一版和未解决的问题
"""
from dataclasses import dataclass
from typing import Any, Sequence, Tuple

DEFAULT_MAX_ROUNDS = 2
MAX_ROUNDS_LIMIT = 3
# 质量问题（篇幅、套话、# 标题等）不超过该数目即视为目标达成
QUALITY_THRESHOLD = 2

MODE_LABELS = {"normal": "逐条修订", "alternate": "换一种修改方式", "minimal": "只处理阻塞问题"}


@dataclass(frozen=True)
class Action:
    kind: str                 # revise 继续修订 / accept 接受 / stop 停止
    mode: str = "normal"      # revise 时的修订方式：normal / alternate / minimal
    outcome: str = ""         # accept、stop 时：goal_met 目标达成 / partial 部分完成
    stop_reason: str = ""     # accept、stop 时：goal_met / no_improvement / rounds_exhausted
    message: str = ""


def counts(report: Any) -> Tuple[int, int]:
    """(阻塞问题数, 质量问题数)"""
    get = report.get if isinstance(report, dict) else (lambda k, d=None: getattr(report, k, d))
    return int(get("blocking_count", 0) or 0), int(get("quality_count", 0) or 0)


def progressed(prev: Any, cur: Any) -> bool:
    """cur 相对 prev 是否有进展：阻塞问题数下降；或阻塞问题已清零时，质量问题数下降"""
    pb, pq = counts(prev)
    cb, cq = counts(cur)
    if cb < pb:
        return True
    return pb == 0 and cb == 0 and cq < pq


def stalls(reports: Sequence[Any]) -> Tuple[int, int]:
    """(末尾连续无进展的轮数, 历史上最长的连续无进展轮数)"""
    trailing = longest = 0
    for prev, cur in zip(reports, reports[1:]):
        trailing = 0 if progressed(prev, cur) else trailing + 1
        longest = max(longest, trailing)
    return trailing, longest


def decide(
    reports: Sequence[Any], rounds_used: int, max_rounds: int, quality_threshold: int = QUALITY_THRESHOLD,
) -> Action:
    """按最后一版的检查结果与修订历史决定：继续修订（及方式）、接受或停止"""
    if not reports:
        raise ValueError("至少需要原稿的检查结果")
    blocking, quality = counts(reports[-1])
    if blocking == 0 and quality <= quality_threshold:
        return Action("accept", outcome="goal_met", stop_reason="goal_met",
                      message="已达成检查目标：没有阻塞问题" + (f"，质量问题 {quality} 个（不超过 {quality_threshold} 个）" if quality else ""))

    trailing, longest = stalls(reports)
    minimal = longest >= 2
    if minimal and blocking == 0:
        return Action("accept", outcome="partial", stop_reason="no_improvement",
                      message=f"连续两轮修订没有进展，按最低目标接受：阻塞问题已清零，仍有 {quality} 个质量问题")
    if rounds_used >= max_rounds:
        left = f"仍有 {blocking} 个阻塞问题、{quality} 个质量问题"
        if trailing:
            return Action("stop", outcome="partial", stop_reason="no_improvement",
                          message=f"修订轮数已用完，最近 {trailing} 轮修订没有进展：{left}")
        return Action("stop", outcome="partial", stop_reason="rounds_exhausted",
                      message=(f"修订轮数已用完（{max_rounds} 轮）：{left}" if max_rounds else f"未设置修订轮数：{left}"))
    mode = "minimal" if minimal else "alternate" if trailing == 1 else "normal"
    return Action("revise", mode=mode, message=f"第 {rounds_used + 1} 轮修订：{MODE_LABELS[mode]}")
