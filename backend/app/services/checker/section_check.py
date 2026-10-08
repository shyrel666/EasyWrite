"""
章节检查器（规则离线可用，模型评审可选）：只检查给定文本，不改动正文。

check_section(project, node, text, evidence) 把候选文本放进虚拟大纲（大纲副本中本节正文替换为该文本），
逐项检查后返回 SectionCheckReport。每个问题标明级别（blocking 阻塞 / quality 质量）、来源（rule / llm）和定位。

| 检查项 | 级别 |
| 本节承接的评分要点未写到 | 阻塞 |
| 承诺数值与全局事实冲突（工期、响应/到场时限、质保期、驻场人数、巡检频次、7×24 等） | 阻塞 |
| 正文出现示例资料，或待核实资料未用【待核实】标注 | 阻塞 |
| 全局事实中没有的具体承诺数值 | 列入待核实清单 |
| 篇幅偏离字数预算超过 ±30% | 质量 |
| 重复本节标题、使用 # 标题、套话与口语化表述 | 质量 |
| 模型评审：无依据的企业事实声明 | 质量（参考；未配置模型或未请求时跳过并注明） |
"""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.core.llm_client import llm_client
from app.models.schemas import (
    CheckIssue, GlobalFacts, OutlineNode, PointCheck, Project, ScoringItem, SectionCheckReport, VerifyItem,
)
from app.services.assets.asset_manager import asset_manager
from app.services.assets.material_check import MATERIAL_KINDS, asset_name
from app.services.checker.quality_inspector import DE_AI_CLICHE_PATTERNS, INFORMAL_OR_WEAK_PHRASES
from app.services.checker.scoring_coverage import is_mentioned
from app.services.generator import rubric_planner as rp
from app.services.generator.evidence_set import EvidenceSet
from app.services.generator.section_generator import strip_title_heading
from app.services.project_store import now_str

logger = logging.getLogger("easywrite.section_check")

LENGTH_TOLERANCE = 0.3
EXCERPT_RADIUS = 18
MAX_LLM_CLAIMS = 10
PLACEHOLDER = re.compile(r"【(?:待填写|待核实)[^】]{0,80}】")
CODE_BLOCK = re.compile(r"```.*?(?:```|$)", re.S)

# 本节自身要求中的评分要点（rubric_planner 生成大纲时写入）："评分子项：X（10分）"/"评分要点：X"/"须覆盖：X"
OWN_POINT = re.compile(r"^(?:评分子项|评分要点|须覆盖)[：:]\s*(.+?)(?:[（(][^）)]*分[^）)]*[）)])?\s*$")


# ---------------- 定位 ----------------

def _locate(text: str, start: int, end: int) -> Tuple[str, int]:
    """(原文片段, 行号)：片段取匹配处前后各 EXCERPT_RADIUS 字，不跨行"""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end
    a, b = max(line_start, start - EXCERPT_RADIUS), min(line_end, end + EXCERPT_RADIUS)
    excerpt = ("…" if a > line_start else "") + text[a:b].strip() + ("…" if b < line_end else "")
    return excerpt, text.count("\n", 0, start) + 1


def _mask(text: str, pattern: re.Pattern) -> str:
    """把匹配到的片段替换为等长空白（保持位置不变，便于定位）"""
    return pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


# ---------------- 评分要点 ----------------

def _flatten(nodes: List[OutlineNode]) -> List[OutlineNode]:
    out: List[OutlineNode] = []
    for n in nodes:
        out.append(n)
        out.extend(_flatten(n.children))
    return out


def _subtree_ids(node: OutlineNode) -> set:
    return {n.id for n in _flatten([node])}


def _ancestor_ids(outline: List[OutlineNode], section_id: str) -> set:
    def walk(nodes, chain):
        for n in nodes:
            if n.id == section_id:
                return chain
            found = walk(n.children, chain + [n.id])
            if found is not None:
                return found
        return None
    return set(walk(outline, []) or [])


def section_points(project: Project, node: OutlineNode) -> List[Tuple[Optional[ScoringItem], str]]:
    """
    本节负责的评分要点 [(评分项, 要点)]：
    1. 本节要求中写明的"评分子项 / 评分要点 / 须覆盖"（按评分细则规划的大纲，拆分到小节的要点在小节自身的要求里）；
    2. 否则取本节直接承接的评分项的子项或评分标准列举的要点——仅当该评分项没有被本节子树与祖先之外的章节分担时
       （多个兄弟章节分担同一评分项、又没有写明各自要点时无法归属，不检查，避免误报）。
    """
    items = {it.id: it for it in rp.target_items(project.tender_analysis)}
    own_items = [items[x] for x in node.scoring_item_ids if x in items]
    own = []
    for req in node.requirements:
        m = OWN_POINT.match(req.strip())
        if m and m.group(1).strip():
            own.append(m.group(1).strip())
    if own:
        first = own_items[0] if len(own_items) == 1 else None
        return [(first, p) for p in dict.fromkeys(own)]

    flat = _flatten(project.outline)
    related = _subtree_ids(node) | _ancestor_ids(project.outline, node.id)
    out = []
    for it in own_items:
        if any(it.id in n.scoring_item_ids for n in flat if n.id not in related):
            continue
        points = [s.name for s in it.sub_items] or rp.content_points(it.criteria)
        out.extend((it, p) for p in points)
    return out


def _subtree_text(node: OutlineNode, text: str) -> str:
    """虚拟大纲：本节正文替换为候选文本，连同子章节的标题与正文一起判断要点是否写到"""
    parts = [node.title, text]
    for child in _flatten(node.children):
        parts += [child.title, child.content]
    return "\n".join(parts)


def _check_points(project: Project, node: OutlineNode, text: str) -> Tuple[List[PointCheck], List[CheckIssue]]:
    scope = _subtree_text(node, text)
    checks, issues = [], []
    for item, point in section_points(project, node):
        mentioned = is_mentioned(point, scope)
        checks.append(PointCheck(item_id=item.id if item else "", item_name=item.name if item else "",
                                 point=point, mentioned=mentioned))
        if not mentioned:
            owner = f"（评分项：{item.name}）" if item else ""
            issues.append(CheckIssue(level="blocking", code="missing_point",
                                     message=f"本节承接的评分要点未写到：{point}{owner}"))
    return checks, issues


# ---------------- 承诺数值 ----------------

CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_int(s: str) -> Optional[float]:
    """一百二十 / 二十四 / 十五 / 两 → 数值；无法解析返回 None"""
    if s == "半":
        return 0.5
    total, current = 0, 0
    for ch in s:
        if ch in CN_DIGIT:
            current = CN_DIGIT[ch]
        elif ch == "十":
            total += (current or 1) * 10
            current = 0
        elif ch == "百":
            total += (current or 1) * 100
            current = 0
        else:
            return None
    return float(total + current)


def _num(s: str) -> Optional[float]:
    try:
        return float(s)
    except ValueError:
        return _cn_int(s)


# 单位 → (量纲, 换算系数)：时限折算为分钟，期限折算为日历天
UNITS = {
    "分钟": ("minute", 1), "小时": ("minute", 60), "个小时": ("minute", 60),
    "工作日": ("workday", 1), "个工作日": ("workday", 1),
    "日历天": ("day", 1), "日历日": ("day", 1), "个日历日": ("day", 1), "个日历天": ("day", 1),
    "自然日": ("day", 1), "天": ("day", 1), "日": ("day", 1), "周": ("day", 7), "个月": ("day", 30), "年": ("day", 365),
    "名": ("person", 1), "人": ("person", 1), "次": ("times", 1),
}
QUANTITY = re.compile(
    r"(\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百]+|半)\s*"
    r"(个?小时|分钟|个?工作日|个?日历[天日]|自然日|天|日|周|个月|年|名|人|次)"
)
WINDOW = re.compile(r"(\d{1,2})\s*[×xX*＊]\s*(\d{1,2})\s*(?:小时|h|H)?")
PERIOD = re.compile(r"每(?:个)?(日|天|周|月|季度|半年|年)")

# 承诺类别：关键词 → 允许的量纲
TOPICS = {
    "duration": (re.compile(r"工期|交付|完工|竣工|建设周期|实施周期|上线|交货"), {"day", "workday"}),
    "warranty": (re.compile(r"质保|保修|维保期|免费维保|免费维护|售后服务期|免费服务期"), {"day"}),
    "response": (re.compile(r"响应"), {"minute", "workday", "day"}),
    "onsite": (re.compile(r"到场|到达现场|抵达现场|赶到现场|到达|上门|赶赴"), {"minute", "workday", "day"}),
    "resolve": (re.compile(r"解决|修复|恢复|排除故障|处理完毕"), {"minute", "workday", "day"}),
    "staff": (re.compile(r"驻场"), {"person"}),
    "inspection": (re.compile(r"巡检"), {"times"}),
}
TOPIC_LABELS = {
    "duration": "工期", "warranty": "质保期", "response": "响应时限", "onsite": "到场时限",
    "resolve": "故障解决时限", "staff": "驻场人数", "inspection": "巡检频次", "window": "服务时间",
}
COMMIT_WORDS = re.compile(r"承诺|保证|确保")
CLAUSE_SPLIT = re.compile(r"[，,。；;！!？?\n]")


def _clause_bounds(text: str, start: int, end: int) -> Tuple[int, int]:
    a = start
    while a > 0 and not CLAUSE_SPLIT.match(text[a - 1]):
        a -= 1
    b = end
    while b < len(text) and not CLAUSE_SPLIT.match(text[b]):
        b += 1
    return a, b


def _topic(text: str, start: int, end: int, dim: str) -> Optional[str]:
    """数值所在分句中距离最近、且与量纲相容的承诺类别关键词"""
    a, b = _clause_bounds(text, start, end)
    best, best_dist = None, None
    for topic, (pattern, dims) in TOPICS.items():
        if dim not in dims:
            continue
        for m in pattern.finditer(text, a, b):
            dist = start - m.end() if m.end() <= start else (m.start() - end if m.start() >= end else 0)
            if best_dist is None or dist < best_dist:
                best, best_dist = topic, dist
    return best


def extract_quantities(text: str, fallback_topic: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    抽取承诺类数值：[{topic, value, label, start, end, committed}]。
    value 已归一（时限为分钟、期限为天、巡检为"周期:次数"、服务时间为"7×24"）；topic 为 None 表示没有承诺类别关键词。
    年份（2026年）、日期（10月1日）、序数（第2年）不算。
    """
    out: List[Dict[str, Any]] = []
    masked = text
    for m in WINDOW.finditer(text):
        out.append({"topic": "window", "value": f"{int(m.group(1))}×{int(m.group(2))}", "label": m.group(0).strip(),
                    "start": m.start(), "end": m.end(), "committed": True, "approx": False})
        masked = masked[:m.start()] + " " * (m.end() - m.start()) + masked[m.end():]
    for m in QUANTITY.finditer(masked):
        raw, unit = m.group(1), m.group(2)
        value = _num(raw)
        if value is None:
            continue
        before = masked[max(0, m.start() - 1):m.start()]
        # 序数（第2年）、频次的周期（每2小时、每半年）、年份（2026年）、日期（10月1日）不是承诺数值
        if before in ("第", "每") or (unit == "年" and value >= 1900) or (unit == "日" and before == "月"):
            continue
        dim, factor = UNITS[unit]
        topic = _topic(masked, m.start(), m.end(), dim) or fallback_topic
        if topic is None and "日历" in unit:
            topic = "duration"  # "N日历天"几乎只用于工期
        if topic and dim not in TOPICS.get(topic, (None, {dim}))[1]:
            topic = None
        if topic == "inspection":
            a, _ = _clause_bounds(masked, m.start(), m.end())
            period = PERIOD.search(masked, a, m.start())
            norm: Any = f"{period.group(1) if period else ''}:{value:g}"
        else:
            norm = round(value * factor, 3) if dim != "workday" else f"workday:{value:g}"
        a, b = _clause_bounds(masked, m.start(), m.end())
        out.append({"topic": topic, "value": norm, "label": m.group(0).strip(), "start": m.start(), "end": m.end(),
                    "committed": bool(COMMIT_WORDS.search(masked, a, b)), "approx": unit in ("个月", "年")})
    return sorted(out, key=lambda q: q["start"])


def same_value(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """数值是否一致：按月、年折算的期限允许 6 天误差（12个月 = 1年 = 365天）"""
    x, y = a["value"], b["value"]
    if isinstance(x, float) and isinstance(y, float) and (a.get("approx") or b.get("approx")):
        return abs(x - y) <= 6
    return x == y


def fact_quantities(facts: GlobalFacts) -> List[Dict[str, Any]]:
    """全局事实中的承诺数值（附来源说明）；工期事实没有关键词时按工期计"""
    sources = [("SLA 售后承诺", facts.sla_commitment, None), ("工期承诺", facts.delivery_guarantee, "duration")]
    sources += [(k, v, None) for k, v in facts.custom_facts.items()]
    out = []
    for label, value, fallback in sources:
        if not (value or "").strip():
            continue
        text = f"{label}：{value}" if fallback is None else value
        for q in extract_quantities(text, fallback):
            if q["topic"]:
                out.append(dict(q, fact=f"{label}：{value.strip()}"))
    return out


def _check_commitments(text: str, facts: GlobalFacts) -> Tuple[List[CheckIssue], List[VerifyItem]]:
    known = fact_quantities(facts)
    issues: List[CheckIssue] = []
    verify: List[VerifyItem] = []
    seen = set()
    for q in extract_quantities(text):
        topic = q["topic"]
        if topic is None and not q["committed"]:
            continue
        excerpt, line = _locate(text, q["start"], q["end"])
        same_topic = [f for f in known if f["topic"] == topic] if topic else []
        if any(same_value(f, q) for f in same_topic) or (topic is None and any(same_value(f, q) for f in known)):
            continue
        label = TOPIC_LABELS.get(topic or "", "承诺数值")
        if same_topic:
            facts_text = "；".join(dict.fromkeys(f["fact"] for f in same_topic))
            issues.append(CheckIssue(level="blocking", code="fact_conflict", excerpt=excerpt, line=line,
                                     message=f"{label}与全局事实不一致：正文为「{q['label']}」，全局事实为「{facts_text}」"))
            continue
        key = (topic, str(q["value"]))
        if key in seen:
            continue
        seen.add(key)
        verify.append(VerifyItem(kind="commitment", text=excerpt, line=line,
                                 note=f"{label}「{q['label']}」不在全局事实中，确认后写入全局事实，或改为【待填写】"))
    return issues, verify


# ---------------- 企业资料 ----------------

def _compact(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _identifiers(kind: str, item: Dict[str, Any]) -> List[str]:
    """用于在正文中识别一条资料的标识：资质名称与证书编号、人员姓名、业绩项目名称"""
    if kind == "qualifications":
        ids = [item.get("name", ""), item.get("cert_no", "")]
    elif kind == "personnel":
        ids = [item.get("name", "")]
    else:
        ids = [item.get("project_name", "")]
    return [x for x in (_compact(i) for i in ids) if len(x) >= 2]


def _compact_index(text: str) -> Tuple[str, List[int]]:
    """去空白文本及其每个字符在原文中的位置"""
    chars, pos = [], []
    for i, ch in enumerate(text):
        if not ch.isspace():
            chars.append(ch)
            pos.append(i)
    return "".join(chars), pos


def _marked(text: str, start: int, end: int, spans: List[Tuple[int, int]]) -> bool:
    """出现处位于【待核实…】内，或同一句中紧随其后有【待核实】标注"""
    if any(a <= start and end <= b for a, b in spans):
        return True
    _, b = _clause_bounds(text, start, end)
    sentence_end = text.find("。", end)
    limit = min(b + 40, sentence_end if sentence_end >= 0 else len(text))
    return "【待核实" in text[end:limit]


def _check_assets(text: str) -> Tuple[List[CheckIssue], List[VerifyItem]]:
    compact, pos = _compact_index(text)
    verify_spans = [(m.start(), m.end()) for m in re.finditer(r"【待核实[^】]{0,80}】", text)]
    issues: List[CheckIssue] = []
    verify: List[VerifyItem] = []
    usable_ids = {i for kind in MATERIAL_KINDS for item in getattr(asset_manager, kind)
                  if item.get("status") != "example" for i in _identifiers(kind, item)}
    for kind in MATERIAL_KINDS:
        for item in getattr(asset_manager, kind):
            status = item.get("status", "confirmed")
            if status not in ("example", "unverified"):
                continue
            for ident in _identifiers(kind, item):
                if status == "example" and ident in usable_ids:
                    continue  # 用户录入了同名资料：以用户资料为准
                idx = compact.find(ident)
                if idx < 0:
                    continue
                start, end = pos[idx], pos[idx + len(ident) - 1] + 1
                excerpt, line = _locate(text, start, end)
                name = asset_name(kind, item)
                if status == "example":
                    issues.append(CheckIssue(level="blocking", code="example_asset", excerpt=excerpt, line=line,
                                             message=f"正文出现预设示例资料「{name}」：示例不是企业的真实资料，须删除或改为企业录入的资料"))
                elif _marked(text, start, end, verify_spans):
                    verify.append(VerifyItem(kind="unverified_asset", text=excerpt, line=line,
                                             note=f"待核实资料「{name}」：核实后到企业资产中改为已确认"))
                else:
                    issues.append(CheckIssue(level="blocking", code="unmarked_unverified", excerpt=excerpt, line=line,
                                             message=f"待核实资料「{name}」未用【待核实】标注：未经企业确认的资料只能写作【待核实：…】"))
                break
    return issues, verify


# ---------------- 质量 ----------------

def _check_quality(text: str, node: OutlineNode) -> List[CheckIssue]:
    issues: List[CheckIssue] = []
    chars = len(re.sub(r"\s+", "", text))
    budget = node.word_budget
    if budget:
        ratio = chars / budget - 1
        if abs(ratio) > LENGTH_TOLERANCE:
            issues.append(CheckIssue(level="quality", code="length",
                                     message=f"篇幅偏离字数预算：{chars} 字 / 预算 {budget} 字（{ratio:+.0%}，允许 ±{LENGTH_TOLERANCE:.0%}）"))
    if strip_title_heading(text, node.title) != text:
        first = text.lstrip("\n").split("\n", 1)[0]
        issues.append(CheckIssue(level="quality", code="title_repeat", excerpt=first.strip(), line=1,
                                 message="开头重复了本节标题：标题由大纲承担，导出时会出现两次"))
    body = _mask(text, CODE_BLOCK)
    for m in re.finditer(r"^[ \t]*#{1,6}[ \t]+\S.*$", body, re.M):
        excerpt, line = _locate(text, m.start(), m.end())
        issues.append(CheckIssue(level="quality", code="markdown_heading", excerpt=excerpt, line=line,
                                 message="使用了 # 标题：正文小标题请用加粗的\"**1.1.1 …**\"形式"))
    for pattern, reason in DE_AI_CLICHE_PATTERNS:
        for m in re.finditer(pattern, body):
            excerpt, line = _locate(text, m.start(), m.end())
            issues.append(CheckIssue(level="quality", code="cliche", excerpt=excerpt, line=line,
                                     message=f"套话「{m.group(0).strip('，, ')}」：{reason}"))
    for weak in INFORMAL_OR_WEAK_PHRASES:
        for m in re.finditer(re.escape(weak), body):
            excerpt, line = _locate(text, m.start(), m.end())
            issues.append(CheckIssue(level="quality", code="weak_phrase", excerpt=excerpt, line=line,
                                     message=f"口语或模糊表述「{weak}」：按企业实际情况写明"))
    return issues


def _placeholders(text: str) -> List[VerifyItem]:
    out, seen = [], set()
    for m in PLACEHOLDER.finditer(text):
        if m.group(0) in seen:
            continue
        seen.add(m.group(0))
        _, line = _locate(text, m.start(), m.end())
        out.append(VerifyItem(kind="placeholder", text=m.group(0), line=line, note="占位待补齐"))
    return out


# ---------------- 模型评审 ----------------

REVIEW_SYSTEM = """你是政企技术标书的审校员。任务：找出【待审正文】中关于投标企业的具体事实声明——资质证书、人员姓名与证书、
业绩项目、获奖荣誉、公司规模，以及响应时限、驻场人数、工期等量化承诺——并判断能否在【全局事实】【企业资料】
【参考资料】中找到依据。只列出找不到依据的声明；【待填写】【待核实】占位不算声明；通用的技术方案描述不算企业事实。
输出 JSON：{"claims": [{"text": "正文原句片段（不超过60字）", "reason": "为什么没有依据"}]}；没有问题时输出 {"claims": []}。"""


def _llm_review(project: Project, text: str, evidence: EvidenceSet) -> Tuple[List[CheckIssue], str, str]:
    """返回 (问题, 状态 done/skipped/failed, 说明)"""
    if not llm_client.is_configured:
        return [], "skipped", "未做模型评审（未配置模型）"
    refs = "\n".join(f"- {r.get('content', '')[:300]}" for r in evidence.refs[:4]) or "（无）"
    user = (f"【全局事实】：\n{project.facts.to_constraint_text()}\n\n"
            f"【企业资料】：\n{evidence.asset_context or '（无）'}\n\n"
            f"【参考资料】：\n{refs}\n\n【待审正文】：\n{text[:12000]}")
    try:
        data = llm_client.chat_completion_structured(REVIEW_SYSTEM, user, purpose="section_review")
    except Exception as e:  # 评审失败不影响规则检查结果
        logger.warning("章节模型评审失败：%s", e)
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("claims"), list):
        return [], "failed", "模型评审失败，仅给出规则检查结果"
    issues = []
    for c in data["claims"][:MAX_LLM_CLAIMS]:
        if not isinstance(c, dict) or not str(c.get("text", "")).strip():
            continue
        claim = str(c["text"]).strip()[:80]
        line = None
        idx = text.find(claim[:20])
        if idx >= 0:
            _, line = _locate(text, idx, idx + len(claim))
        issues.append(CheckIssue(level="quality", source="llm", code="unsupported_claim", excerpt=claim, line=line,
                                 message=f"模型评审：无依据的企业事实声明——{str(c.get('reason', '')).strip() or '未找到依据'}"))
    return issues, "done", f"模型评审完成，发现 {len(issues)} 处无依据声明（仅供参考）"


# ---------------- 入口 ----------------

def check_section(
    project: Project, node: OutlineNode, text: str,
    evidence: Optional[EvidenceSet] = None, llm_review: bool = False,
) -> SectionCheckReport:
    """检查 node 的候选文本 text（不改动正文）；llm_review=True 且已配置模型时加做模型评审"""
    evidence = evidence or EvidenceSet()
    report = SectionCheckReport(section_id=node.id, section_title=node.title, checked_at=now_str(),
                                char_count=len(re.sub(r"\s+", "", text or "")), word_budget=node.word_budget)
    if not (text or "").strip():
        report.issues = [CheckIssue(level="blocking", code="empty", message="正文为空")]
        report.blocking_count = 1
        report.llm_note = "正文为空，未做模型评审"
        return report

    analysed = _mask(text, CODE_BLOCK)  # 架构图代码不参与数值与资料检查
    points, point_issues = _check_points(project, node, text)
    commitment_issues, commitment_verify = _check_commitments(_mask(analysed, PLACEHOLDER), project.facts)
    asset_issues, asset_verify = _check_assets(analysed)
    issues = point_issues + commitment_issues + asset_issues + _check_quality(text, node)
    verify = commitment_verify + asset_verify + _placeholders(text)

    if llm_review:
        llm_issues, state, note = _llm_review(project, text, evidence)
        issues += llm_issues
        report.llm_review, report.llm_note = state, note
        report.mode = "llm" if state == "done" else "rules"
    else:
        report.llm_note = "未做模型评审（仅规则检查）"

    report.issues = issues
    report.pending_verification = verify
    report.points = points
    report.blocking_count = sum(1 for i in issues if i.level == "blocking")
    report.quality_count = sum(1 for i in issues if i.level == "quality")
    report.passed = report.blocking_count == 0
    return report
