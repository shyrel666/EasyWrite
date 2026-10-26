"""
章节检查器（规则离线可用，模型评审可选）：只检查给定文本，不改动正文。

check_section(project, node, text, evidence) 把候选文本放进虚拟大纲（大纲副本中本节正文替换为该文本），
逐项检查后返回 SectionCheckReport。每个问题标明级别（blocking 阻塞 / quality 质量）、来源（rule / llm）和定位。

| 检查项 | 级别 |
| 本节承接的评分要点未写到 | 阻塞 |
| 承诺数值与全局事实冲突（工期、响应/到场时限、质保期、驻场人数、巡检频次、7×24 等） | 阻塞 |
| 承诺数值不低于全局事实承诺（分阶段里程碑、分级时限、分级巡检等合法写法） | 列入待核实清单 |
| 正文出现示例资料，或待核实资料未用【待核实】标注 | 阻塞 |
| 人员在职、劳动关系、社保缴纳等现状断言没有人员资料支撑（未用【待核实】标注） | 阻塞 |
| 全局事实中没有的具体承诺数值 | 列入待核实清单 |
| 篇幅偏离字数预算超过 ±30%（正文字数，不计 Mermaid 图代码与 Markdown 符号） | 质量 |
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
from app.services.assets.material_check import MATERIAL_KINDS, asset_name
from app.services.checker.placeholders import PLACEHOLDER, VERIFY_MARK
from app.services.checker.quality_inspector import DE_AI_CLICHE_PATTERNS, INFORMAL_OR_WEAK_PHRASES
from app.services.checker.scoring_coverage import is_mentioned
from app.services.generator import rubric_planner as rp
from app.services.generator.evidence_set import EvidenceSet
from app.services.generator.section_generator import strip_title_heading
from app.services.project_store import now_str
from app.services.text_stats import body_chars, diagram_chars

logger = logging.getLogger("easywrite.section_check")

LENGTH_TOLERANCE = 0.3
EXCERPT_RADIUS = 18
MAX_LLM_CLAIMS = 10
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
# 从业年限不是工期：评估 2026-10-25-2 中"上线支持 | 周敏（从业6年）"曾因分句含"上线"被判工期冲突
EXPERIENCE_BEFORE = re.compile(r"(?:从业|工龄|经验)[^，。；;\n]{0,4}$")   # 从业…N年（年限在词后）
EXPERIENCE_AFTER = re.compile(r"[^，。；;\n]{0,4}(?:从业|经验)")        # N年…经验（年限在词前）

# 承诺类别：关键词 → 允许的量纲
TOPICS = {
    "duration": (re.compile(r"工期|交付|完工|竣工|建设周期|实施周期|上线|交货"), {"minute", "day", "workday"}),
    "warranty": (re.compile(r"质保|保修|维保期|免费维保|免费维护|售后服务期|免费服务期"), {"minute", "day"}),
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
# 具体动作词优先于泛化词：分句里同时出现"响应"与"到达现场"时，数值判为到场时限
# （评估 2026-10-25-2：全局事实含多条时限时，"…响应机制…4小时内到达现场"曾被误判为响应时限与事实冲突）
SPECIFIC_TOPICS = ("duration", "warranty", "onsite", "resolve", "staff", "inspection")


def _clause_bounds(text: str, start: int, end: int) -> Tuple[int, int]:
    a = start
    while a > 0 and not CLAUSE_SPLIT.match(text[a - 1]):
        a -= 1
    b = end
    while b < len(text) and not CLAUSE_SPLIT.match(text[b]):
        b += 1
    return a, b


def _topic(text: str, start: int, end: int, dim: str) -> Optional[str]:
    """数值所在分句中距离最近、且与量纲相容的承诺类别关键词；具体动作词（到场/解决/工期等）
    比泛化词（响应）优先——同为候选时取具体词，避免到场时限被误归为响应时限"""
    a, b = _clause_bounds(text, start, end)
    best, best_key = None, None
    for topic, (pattern, dims) in TOPICS.items():
        if dim not in dims:
            continue
        for m in pattern.finditer(text, a, b):
            dist = start - m.end() if m.end() <= start else (m.start() - end if m.start() >= end else 0)
            key = (0 if topic in SPECIFIC_TOPICS else 1, dist)
            if best_key is None or key < best_key:
                best, best_key = topic, key
    return best


ASCII_ALNUM = re.compile(r"[A-Za-z0-9]")
SEVERITY = re.compile(r"[一二三四五1-5]级(?:重大|严重|一般|轻微)?(?:故障|事件)?|(?:重大|严重|一般|紧急|轻微)故障|P[0-5]", re.I)


def scope_key(q: Dict[str, Any]) -> tuple:
    return tuple(q.get(k, "") for k in ("severity", "scope", "subject"))


def _quantity_context(text: str, q: Dict[str, Any], known=()) -> Dict[str, Any]:
    a, b = _clause_bounds(text, q["start"], q["end"])
    clause = text[a:b]
    levels = list(SEVERITY.finditer(clause))
    q["severity"] = min(levels, key=lambda m: abs(m.start() - (q["start"] - a))).group() if levels else ""
    # 主体/场景只接受明确出现的已知标签；不靠数值猜测归属。
    for key in ("scope", "subject"):
        matches = {f[key] for f in known if f.get(key) and f[key] in clause}
        q[key] = next(iter(matches)) if len(matches) == 1 else ("?" if matches else "")
    known_levels = {f["severity"] for f in known if f.get("severity") and f["severity"] in clause}
    if len(known_levels) == 1:
        q["severity"] = next(iter(known_levels))
    elif len(known_levels) > 1:
        q["severity"] = "?"
    prefix = text[max(a, q["start"] - 8):q["start"]]
    q["comparator"] = "ge" if re.search(r"不少于|至少|不低于|≥", prefix) else "le" if re.search(r"不超过|至多|不多于|≤", prefix) else "eq"
    return q


def _ascii_alnum(ch: str) -> bool:
    """仅 ASCII 字母数字（中文不属于：'供'.isalnum() 为 True，会把正常中文语境全部误杀）"""
    return bool(ch) and bool(ASCII_ALNUM.match(ch))


def extract_quantities(text: str, fallback_topic: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    抽取承诺类数值：[{topic, value, label, start, end, committed}]。
    value 已归一（时限为分钟、期限为天、巡检为"周期:次数"、服务时间为"7×24"）；topic 为 None 表示没有承诺类别关键词。
    年份（2026年）、日期（10月1日）、序数（第2年）不算。
    """
    out: List[Dict[str, Any]] = []
    masked = text
    for m in WINDOW.finditer(text):
        # 统一社会信用代码等 ASCII 字母数字串里的"N X M"不是服务窗口（评估 2026-10-25-2：91500108MA61U8X2XK 撞出"8X2"）；
        # 中文语境（提供7×24小时服务）不受影响
        if _ascii_alnum(text[max(0, m.start() - 1):m.start()]) or _ascii_alnum(text[m.end():m.end() + 1]):
            continue
        out.append({"topic": "window", "value": f"{int(m.group(1))}×{int(m.group(2))}", "label": m.group(0).strip(),
                    "start": m.start(), "end": m.end(), "committed": True, "approx": False, "dimension": "window"})
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
        # 从业年限（从业6年 / 6年经验）是人员事实的转述，不是工期或期限承诺
        if unit == "年" and (EXPERIENCE_BEFORE.search(text[:m.start()]) or EXPERIENCE_AFTER.match(text[m.end():])):
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
            if not period:
                topic = None  # 无周期的"一次"是动量用法（开展一次演练、一次解决率），不是巡检频次承诺
                norm = value
            else:
                norm: Any = f"{period.group(1)}:{value:g}"
        else:
            norm = round(value * factor, 3) if dim != "workday" else f"workday:{value:g}"
            if topic in ("response", "onsite", "resolve") and dim == "day":
                norm, dim = round(norm * 1440, 3), "minute"
            elif topic in ("duration", "warranty") and dim == "minute":
                norm, dim = round(norm / 1440, 3), "day"
        a, b = _clause_bounds(masked, m.start(), m.end())
        out.append({"topic": topic, "value": norm, "label": m.group(0).strip(), "start": m.start(), "end": m.end(),
                    "committed": bool(COMMIT_WORDS.search(masked, a, b)), "approx": unit in ("个月", "年"),
                    "dimension": dim})
    return [_quantity_context(text, q) for q in sorted(out, key=lambda q: q["start"])]


def same_value(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """数值是否一致：按月、年折算的期限允许 6 天误差（12个月 = 1年 = 365天）"""
    x, y = a["value"], b["value"]
    if a.get("dimension") != b.get("dimension"):
        return False
    if isinstance(x, float) and isinstance(y, float) and (a.get("approx") or b.get("approx")):
        return abs(x - y) <= (6 * 1440 if a.get("dimension") == "minute" else 6)
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
    structured = []
    for c in facts.commitments:
        if not c.confirmed:
            continue
        value_text = f"{c.value}{c.unit}"
        if c.topic == "inspection" and c.unit.startswith("次/"):
            value_text = f"每{c.unit[2:]}{c.value}次"
        for q in extract_quantities(f"{TOPIC_LABELS[c.topic]} {value_text}", c.topic):
            if q["topic"] == c.topic:
                structured.append(dict(q, fact=c.description(), fact_key=c.id, comparator=c.comparator,
                                       severity=c.severity, scope=c.scope, subject=c.subject))
    # 精确作用域的结构化记录优先，旧文本仍支持未被迁移的作用域。
    scopes = {(q["topic"], scope_key(q)) for q in structured}
    out = [q for q in out if (q["topic"], scope_key(q)) not in scopes]
    out.extend(structured)
    return out


# 巡检频次归一：单位时间内的次数（次/天），次数多 = 更优
PERIOD_DAYS = {"日": 1, "天": 1, "周": 7, "月": 30, "季度": 90, "半年": 180, "年": 365}


def _better_or_equal(fact: Dict[str, Any], q: Dict[str, Any]) -> Optional[bool]:
    """正文数值是否不劣于事实承诺：时限/期限更短、服务窗口更宽、巡检更频为优。不可比（如工作日与日历天）返回 None。

    评估 2026-10-25-2：分阶段里程碑（进场 7 日历天 < 总工期 30 日历天）、分级巡检（日检相对事实的月检）
    是常见且合法的写法，原判定一律记"与全局事实不一致"，制造大量阻塞误报；不劣于事实的取值改为列入待核实。"""
    x, y = fact["value"], q["value"]
    if fact.get("dimension") != q.get("dimension"):
        return None
    if isinstance(x, float) and isinstance(y, float):
        # 时限/工期/期限：更短为优；质保期相反：更长为优（对采购人更有利）
        return y >= x if fact.get("topic") in ("warranty", "staff") else y <= x
    if isinstance(x, str) and isinstance(y, str) and "×" in x and "×" in y:  # 服务窗口 N×M：每周覆盖时长更宽为优
        try:
            ax, bx = (int(v) for v in x.split("×"))
            ay, by = (int(v) for v in y.split("×"))
        except ValueError:
            return None
        return ay * by >= ax * bx
    if isinstance(x, str) and isinstance(y, str) and ":" in x and ":" in y:  # 巡检频次 周期:次数
        px, _, cx = x.partition(":")
        py, _, cy = y.partition(":")
        if px == py == "workday":
            return float(cy) <= float(cx)
        if px not in PERIOD_DAYS or py not in PERIOD_DAYS:
            return None
        try:
            per_fact = float(cx) / PERIOD_DAYS[px]
            per_text = float(cy) / PERIOD_DAYS[py]
        except ValueError:
            return None
        return per_text >= per_fact
    return None


def commitment_relation(q: Dict[str, Any], known: List[Dict[str, Any]]) -> str:
    """单章与全书共用的判定；不可比的单位不应被武断标为事实冲突。"""
    related = [f for f in known if f["topic"] == q["topic"]] if q["topic"] else known
    candidates = [f for f in related if scope_key(f) == scope_key(q)]
    if related and not candidates:
        return "scope_unknown"
    if any(f.get("comparator", "eq") != q.get("comparator", "eq")
           and {f.get("comparator", "eq"), q.get("comparator", "eq")} == {"le", "ge"} for f in candidates):
        return "conflict"
    if any(same_value(f, q) for f in candidates):
        return "backed"
    if not candidates or not q["topic"]:
        return "unbacked"
    comparisons = [_better_or_equal(f, q) for f in candidates]
    if any(v is True for v in comparisons):
        return "stronger"
    if any(v is None for v in comparisons):
        return "incomparable"
    return "conflict"


def _check_commitments(text: str, facts: GlobalFacts) -> Tuple[List[CheckIssue], List[VerifyItem]]:
    known = fact_quantities(facts)
    issues: List[CheckIssue] = []
    verify: List[VerifyItem] = []
    seen = set()
    for q in extract_quantities(text):
        _quantity_context(text, q, known)
        topic = q["topic"]
        if topic is None and not q["committed"]:
            continue
        excerpt, line = _locate(text, q["start"], q["end"])
        same_topic = [f for f in known if f["topic"] == topic] if topic else []
        relation = commitment_relation(q, known)
        if relation == "backed":
            continue
        label = TOPIC_LABELS.get(topic or "", "承诺数值")
        if same_topic:
            if relation in ("stronger", "incomparable", "scope_unknown"):
                # 优于或不劣于事实承诺：分阶段里程碑、分级时限与分级巡检常见于此，不算冲突，列入待核实
                key = (topic, str(q["value"]), scope_key(q))
                if key not in seen:
                    seen.add(key)
                    note = ("不低于全局事实承诺，属分阶段/分档安排，确认后保留" if relation == "stronger"
                            else "级别、场景或主体不能映射到已确认事实，请人工核实" if relation == "scope_unknown"
                            else "与全局事实的单位不可直接比较，请核实工作日与自然日等适用口径")
                    verify.append(VerifyItem(kind="commitment", text=excerpt, line=line,
                                             note=f"{label}「{q['label']}」{note}"))
                continue
            facts_text = "；".join(dict.fromkeys(f["fact"] for f in same_topic))
            issues.append(CheckIssue(level="blocking", code="fact_conflict", excerpt=excerpt, line=line,
                                     message=f"{label}与全局事实不一致：正文为「{q['label']}」，全局事实为「{facts_text}」"))
            continue
        key = (topic, str(q["value"]), scope_key(q))
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
    """出现处位于【…待核实…】内，或同一句中紧随其后有【…待核实…】标注"""
    if any(a <= start and end <= b for a, b in spans):
        return True
    _, b = _clause_bounds(text, start, end)
    boundary = re.search(r"[。；;\n]", text[end:])
    limit = min(b + 40, end + boundary.start() if boundary else len(text))
    return VERIFY_MARK.search(text[end:limit]) is not None


def _check_assets(text: str, assets: Dict) -> Tuple[List[CheckIssue], List[VerifyItem]]:
    compact, pos = _compact_index(text)
    verify_spans = [(m.start(), m.end()) for m in VERIFY_MARK.finditer(text)]
    issues: List[CheckIssue] = []
    verify: List[VerifyItem] = []
    usable_ids = {i for kind in MATERIAL_KINDS for item in assets.get(kind, [])
                  if item.get("status") != "example" for i in _identifiers(kind, item)}
    for kind in MATERIAL_KINDS:
        for item in assets.get(kind, []):
            status = item.get("status", "confirmed")
            if status not in ("example", "unverified"):
                continue
            occurrences = set()
            for ident in _identifiers(kind, item):
                if status == "example" and ident in usable_ids:
                    continue  # 用户录入了同名资料：以用户资料为准
                for match in re.finditer(re.escape(ident), compact):
                    occurrences.add((pos[match.start()], pos[match.end() - 1] + 1))
            checked_spans = []
            for start, end in sorted(occurrences, key=lambda pair: (pair[0], -pair[1])):
                if any(a <= start and end <= b for a, b in checked_spans):
                    continue  # 资料名称与证书编号在同一位置重叠时只报一次
                checked_spans.append((start, end))
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
    return issues, verify


# ---------------- 人员现状断言 ----------------

# 在职、劳动关系、社保缴纳是企业现状事实，不能由招标要求推导：人员资料没有依据时不得作肯定性断言，
# 只能写"拟派/拟投入"的计划性表述或用【待核实】占位（评估报告 2026-10-09 优先问题：
# 企业资料为 0 时正文仍声称"全部为直接聘用并依法缴纳社会保险的在职员工"）
EMPLOYMENT_CLAIM = re.compile(
    r"(?:依法?缴纳[^，。；;\n]{0,6}社会保险|社会保险缴纳单位|社保缴纳单位|签订[^，。；;\n]{0,8}劳动合同"
    r"|直接聘用|正式在职|在职员工|在职人员|自有员工|自有人员|专职员工)"
)
# 计划性表述（中标后怎么做）是对未来的承诺，不是对企业现状的断言
EMPLOYMENT_PLAN_GUARD = re.compile(r"中标后|合同签订后|签订合同后|录用后|入职后|进场后|拟派|拟聘|拟投入|拟任")


def _check_employment(text: str, assets: Dict) -> List[CheckIssue]:
    """人员资料库没有可用条目（示例资料不算）时，正文中带肯定性语气的在职/劳动关系/社保断言均为无依据。

    录入了人员资料时不检查：断言是否与资料相符交给模型评审判断，规则层没有判据，避免误报。"""
    if any(i.get("status") != "example" for i in assets.get("personnel", [])):
        return []
    verify_spans = [(m.start(), m.end()) for m in VERIFY_MARK.finditer(text)]
    issues: List[CheckIssue] = []
    last_clause = (-1, -1)
    for m in EMPLOYMENT_CLAIM.finditer(text):
        a, b = _clause_bounds(text, m.start(), m.end())
        if a < last_clause[1] and b > last_clause[0]:
            continue  # 同一分句只报一次（"直接聘用并依法缴纳社会保险的在职员工"命中三处）
        if _marked(text, m.start(), m.end(), verify_spans):
            continue
        if EMPLOYMENT_PLAN_GUARD.search(text, a, b):
            continue
        last_clause = (a, b)
        excerpt, line = _locate(text, m.start(), m.end())
        issues.append(CheckIssue(level="blocking", code="employment_claim", excerpt=excerpt, line=line,
                                 message="人员在职、劳动关系、社保缴纳等企业现状断言没有人员资料支撑："
                                         "只能写“拟派/拟投入”的计划性表述，或改为【待核实：…】"))
    return issues


# ---------------- 质量 ----------------

def _check_quality(text: str, node: OutlineNode) -> List[CheckIssue]:
    issues: List[CheckIssue] = []
    chars = body_chars(text)
    budget = node.word_budget
    if node.diagram_budget and diagram_chars(text) > node.diagram_budget:
        issues.append(CheckIssue(level="quality", code="diagram_length", message=f"图表代码超出独立预算：{diagram_chars(text)} / {node.diagram_budget} 字符"))
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

def revision_issues(report: SectionCheckReport, instruction: str = "", blocking_only: bool = False) -> List[str]:
    """定向修订的问题清单：规则检查出的问题（附原文定位；blocking_only 时只取阻塞问题）+ 用户补充的修订要求"""
    issues = [i.message + (f"（原文：{i.excerpt}）" if i.excerpt else "")
              for i in report.issues if i.source == "rule" and (i.level == "blocking" or not blocking_only)]
    if instruction.strip():
        issues.append(f"补充修订要求：{instruction.strip()}")
    return issues


def check_section(
    project: Project, node: OutlineNode, text: str,
    evidence: Optional[EvidenceSet] = None, llm_review: bool = False,
) -> SectionCheckReport:
    """检查 node 的候选文本 text（不改动正文）；llm_review=True 且已配置模型时加做模型评审"""
    evidence = evidence or EvidenceSet()
    report = SectionCheckReport(section_id=node.id, section_title=node.title, checked_at=now_str(),
                                char_count=body_chars(text), word_budget=node.word_budget,
                                diagram_chars=diagram_chars(text), diagram_budget=node.diagram_budget,
                                length_deviation=body_chars(text) / node.word_budget - 1 if node.word_budget else 0,
                                excess_chars=max(0, body_chars(text) - node.word_budget) if node.word_budget else 0)
    if not (text or "").strip():
        report.issues = [CheckIssue(level="blocking", code="empty", message="正文为空")]
        report.blocking_count = 1
        report.llm_note = "正文为空，未做模型评审"
        return report

    analysed = _mask(text, CODE_BLOCK)  # 架构图代码不参与数值与资料检查
    snapshot = evidence.asset_snapshot()
    points, point_issues = _check_points(project, node, text)
    commitment_issues, commitment_verify = _check_commitments(_mask(analysed, PLACEHOLDER), project.facts)
    asset_issues, asset_verify = _check_assets(analysed, snapshot)
    employment_issues = _check_employment(analysed, snapshot)
    issues = point_issues + commitment_issues + asset_issues + employment_issues + _check_quality(text, node)
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
