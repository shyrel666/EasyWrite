"""
证明材料检查（纯规则，离线可用）：一条资质/人员/业绩资料能否作为评分项的证明材料。

检查项：示例资料、待核实、无附件、有效期（已过期 / 将在投标截止日前过期 / 无法识别）、
所属主体与投标人全称不一致。只依据用户录入的资料与项目全局事实，不调用模型、不推测。
"""
import calendar
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from app.models.schemas import NOT_MENTIONED, GlobalFacts, MaterialCheck, MaterialProblem, TenderAnalysis18

MATERIAL_KINDS = ("qualifications", "personnel", "cases")

# 问题代码 → 证明材料状态；状态按严重程度排列，多个问题时取最严重的一个
PROBLEM_STATUS = {
    "example": "示例资料",
    "expired": "过期",
    "expiring": "过期",
    "holder_mismatch": "主体不符",
    "no_attachment": "缺附件",
    "unverified": "待核实",
    "expiry_unknown": "待核实",
}
STATUS_ORDER = ["示例资料", "过期", "主体不符", "缺附件", "待核实", "齐备"]
COMPLETE = "齐备"
NOT_LINKED = "未关联"

LONG_TERM = re.compile(r"长期|永久|无固定期限")
_FULL_DATE = re.compile(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})")
_YEAR_MONTH = re.compile(r"(\d{4})\s*[-/.年]\s*(\d{1,2})(?!\d)")


def parse_date(text: str, month_end: bool = False) -> Optional[date]:
    """解析 2026-11-14 / 2026/11/14 / 2026.11.14 / 2026年11月14日；只有年月时，month_end 取当月最后一天"""
    if not text:
        return None
    m = _FULL_DATE.search(text)
    try:
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = _YEAR_MONTH.search(text)
        if m:
            year, month = int(m.group(1)), int(m.group(2))
            day = calendar.monthrange(year, month)[1] if month_end else 1
            return date(year, month, day)
    except ValueError:
        return None
    return None


def bid_deadline(analysis: Optional[TenderAnalysis18], today: Optional[date] = None) -> Tuple[date, str]:
    """投标截止日：取自拆标结果的"投标截止时间"，解析不出时用当天。返回 (日期, 来源说明)"""
    today = today or date.today()
    if analysis is None:
        return today, "当天"
    raw = analysis.submission_deadline or ""
    if raw and raw != NOT_MENTIONED:
        parsed = parse_date(raw)
        if parsed:
            return parsed, "投标截止时间"
    return today, "当天，拆标结果中未识别到投标截止时间"


def _norm_name(text: str) -> str:
    return re.sub(r"[\s()（）【】\[\]·•,，.。]", "", text or "")


def asset_name(kind: str, asset: Dict[str, Any]) -> str:
    return asset.get("project_name") if kind == "cases" else asset.get("name", "")


def check_material(
    kind: str,
    asset: Dict[str, Any],
    facts: Optional[GlobalFacts] = None,
    deadline: Optional[date] = None,
    today: Optional[date] = None,
) -> MaterialCheck:
    """
    检查一条资料作为证明材料是否齐备。asset 为资料的字典形式（asset_manager 存储格式）。
    deadline：投标截止日（见 bid_deadline）；facts 未提供或投标人全称为空时不核对所属主体。
    """
    today = today or date.today()
    deadline = deadline or today
    problems: List[MaterialProblem] = []

    def add(code: str, message: str):
        problems.append(MaterialProblem(code=code, message=message))

    status = asset.get("status", "confirmed")
    if status == "example":
        add("example", "预设示例资料（虚构），不能作为证明材料")
    elif status == "unverified":
        add("unverified", "资料待核实，尚未经企业确认")

    attachments = asset.get("attachments") or []
    if not attachments:
        add("no_attachment", "未上传证明附件")

    if kind == "qualifications":
        expiry_text = (asset.get("expiry_date") or "").strip()
        if expiry_text and not LONG_TERM.search(expiry_text):
            expiry = parse_date(expiry_text, month_end=True)
            if expiry is None:
                add("expiry_unknown", f"有效期「{expiry_text}」无法识别，请人工核对")
            elif expiry < today:
                add("expired", f"证书已于 {expiry.isoformat()} 过期")
            elif expiry < deadline:
                add("expiring", f"证书将于 {expiry.isoformat()} 到期，早于投标截止日 {deadline.isoformat()}")

    holder = (asset.get("holder") or "").strip()
    company = (facts.company_name if facts else "").strip()
    if holder and company and company != NOT_MENTIONED and _norm_name(holder) != _norm_name(company):
        add("holder_mismatch", f"所属主体「{holder}」与投标人全称「{company}」不一致")

    return MaterialCheck(
        kind=kind,
        asset_id=asset.get("id", ""),
        name=asset_name(kind, asset) or "",
        asset_status=status,
        status=worst_status(PROBLEM_STATUS[p.code] for p in problems),
        problems=problems,
        attachment_count=len(attachments),
    )


def worst_status(statuses) -> str:
    """多个状态取最严重的一个；没有问题时为"齐备" """
    found = set(statuses)
    for s in STATUS_ORDER:
        if s in found:
            return s
    return COMPLETE
