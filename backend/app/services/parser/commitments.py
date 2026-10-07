"""
承诺建议：把拆标结果中的工期、质保等要求整理成"建议写入全局事实"的措辞。

全局事实会作为硬约束注入每一章，因此这里只产出建议，不写入项目；
措辞只复述招标原文要求，不添加招标文件以外的承诺（如自行补上的响应时限）。
"""
import re
from typing import List, Optional

from app.models.schemas import NOT_MENTIONED, CommitmentSuggestion, TenderAnalysis18

# 原文本身已是完整表述（含动作）时直接沿用，否则套用"X为…"的复述句式
_SENTENCE_HINT = re.compile(r"完成|交付|上线|验收|竣工|提供|承担|负责|保修|维护|运维")

# (拆标字段, 目标事实字段, 事实名称, 复述句式)
COMMITMENT_RULES = (
    ("duration_requirement", "delivery_guarantee", "工期交付承诺", "工期为{value}"),
    ("warranty_period", "sla_commitment", "SLA 售后承诺", "质保期为{value}"),
)


def _clean(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip().rstrip("。；;，,")
    return "" if value in ("", NOT_MENTIONED) else value


def suggest_commitments(analysis: Optional[TenderAnalysis18]) -> List[CommitmentSuggestion]:
    """纯函数：按拆标结果生成承诺建议（未提及的要求不生成）"""
    if not analysis:
        return []
    suggestions = []
    for source_field, field, label, pattern in COMMITMENT_RULES:
        value = _clean(getattr(analysis, source_field, ""))
        if not value:
            continue
        body = value if _SENTENCE_HINT.search(value) else pattern.format(value=value)
        suggestions.append(CommitmentSuggestion(
            field=field, field_label=label, source_field=source_field,
            requirement=value, suggestion=f"按招标要求，{body}",
        ))
    return suggestions
