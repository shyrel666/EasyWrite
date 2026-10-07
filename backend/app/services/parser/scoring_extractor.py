"""
评分细则结构化抽取（强规则：直接解析招标文件中的评分表，不经过大模型）。

政府采购招标文件的评分表高度格式化（序号 | 评分因素及权重 | 分值 | 评分标准 | 说明），
但真实文件存在大量变体，以下均来自 ccgp 公开招标文件实测：
- 分值单元格写法：「技术服务方案 （50分）」「20分」「30」「基础服务20」「服务 方案 12」「现场 演示24分」「4.5分」
- 评分项名称可能只出现在评分标准首句：「1. 项目理解 投标人对……」
- 一个评分项跨多行（分值单元格纵向合并） vs 相邻多项同为「10分」——靠物理单元格编号区分
- 多分包项目每包一张评分表（「包一评审因素」「（二）包2评审因素」）
- 资格/符合性审查表（无分值列）与履约考核表（扣分标准）不是评分表

纪律：识别不了的分值留空不臆测；每包分值合计与 100 核对，不一致时明确提示人工核对。
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from app.models.schemas import ScoringItem, ScoringSubItem, TenderAnalysis18, NOT_MENTIONED

HEADER_POINTS = re.compile(r"^(?:分值|分数|满分|标准分|权重分值)")
HEADER_CRITERIA = re.compile(r"评分标准|评审标准|评标标准|评分细则|评审细则|评分办法|评分说明|评分要求|具体要求")
HEADER_CRITERIA_WEAK = re.compile(r"评分内容|评审内容")  # 也常作大类列名，没有更明确的评分标准列时才算
HEADER_CATEGORY = re.compile(r"评分因素|评审因素|评分项|评审项|评分类别|评审类别|评分内容|评审内容|项目|类别")
HEADER_NOTE = re.compile(r"说明|备注|证明材料|佐证")
HEADER_EXCLUDE = re.compile(r"扣分标准|考核")

NUM = r"(\d+(?:\.\d+)?)"
CN_DIGITS = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
# 「包2」「分包二」「第1包」「A包」「包B」
PACKAGE_RE = re.compile(
    r"第\s*([一二三四五六七八九十\d]{1,2})\s*(?:分包|包)"
    r"|(?:分包|包)\s*([一二三四五六七八九十\d]{1,2}|[A-Za-z](?![A-Za-z]))"
    r"|(?<![A-Za-z])([A-Za-z])\s*(?:分包|包)"
)
# 分值单元格里的可选分值档：「10.0（0.0-10.0）」「3.0（0,1,2,3）」「6.0（0,1,2,3,4,5, 6）」
POINT_STEPS = re.compile(r"\s*[（(][\d.,，、\s\-—~～]+[）)]\s*$")
SUB_ITEM_RE = re.compile(
    r"(?:^|[\s。；;])(?:\d{1,2}[.、．]|[（(]\d{1,2}[）)])\s*([^\s（(：:，,。；;]{2,20}?)\s*[（(]\s*" + NUM + r"\s*分\s*[）)]"
)
CJK_SPACE = re.compile(r"(?<=[一-鿿])\s+(?=[一-鿿])")


def _clean(text: str) -> str:
    """去掉单元格换行造成的中文字间空格：「服务 方案」→「服务方案」"""
    return CJK_SPACE.sub("", (text or "").strip())


def _to_float(s: str) -> Optional[float]:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_points_cell(text: str) -> Tuple[str, Optional[float]]:
    """拆分分值单元格为（名称, 分值）：「技术服务方案 （50分）」→（技术服务方案, 50）；「20分」→（"", 20）"""
    t = _clean(text)
    if not t:
        return "", None
    if re.match(NUM, t) and POINT_STEPS.search(t):
        t = POINT_STEPS.sub("", t)
    m = re.match(r"^(.*?)[\s（(]*" + NUM + r"\s*分?\s*[）)]?$", t)
    if m:
        return m.group(1).strip(" ：:（("), _to_float(m.group(2))
    return t, None


def _split_category(text: str) -> Tuple[str, Optional[float]]:
    """「技术部分 60%」→（技术部分, 60）"""
    t = _clean(text)
    m = re.search(NUM + r"\s*[%％]", t)
    weight = _to_float(m.group(1)) if m else None
    name = re.sub(r"[（(]?\s*" + NUM + r"\s*[%％]\s*[）)]?", "", t).strip()
    return name, weight


# 评分标准首句不像标题时，按关键词给出可读名称（先匹配者优先）
NAME_KEYWORDS = [
    (r"投标报价|评标基准价|价格分", "投标报价"),
    (r"项目负责人|项目经理", "项目负责人资质"),
    (r"技术负责人", "技术负责人资质"),
    (r"其他成员|团队成员|项目团队|拟派.*人员", "团队人员配置"),
    (r"业绩|案例", "类似项目业绩"),
    (r"体系认证|认证证书|资质证书", "企业资质认证"),
    (r"著作权", "软件著作权"),
]


def _name_from_criteria(criteria: str) -> str:
    """
    评分标准首句提取名称：「1. 项目理解 投标人对……」→ 项目理解；
    首句是长句或日期（「2023年1月1日以来……」）时按关键词命名，仍不行则返回空串由调用方兜底
    """
    t = (criteria or "").strip()
    t = re.sub(r"^(?:\d{1,2}\s*[.、．]|[（(]\d{1,2}[）)])\s*", "", t)
    first = re.split(r"[\s，,。；;：:（(]", t, maxsplit=1)[0]
    if 2 <= len(first) <= 12 and not re.match(r"^(?:19|20)\d\d", first):
        return first
    for pattern, name in NAME_KEYWORDS:
        if re.search(pattern, criteria[:120]):
            return name
    return ""


def _classify_kind(category: str, name: str) -> str:
    text = category + name
    if re.search(r"报价|价格", text):
        return "price"
    if re.search(r"商务|资信|信誉", category):
        return "business"
    if re.search(r"技术|服务|质量|方案", category):
        return "technical"
    if re.search(r"业绩|资质|人员|团队|证书", name):
        return "business"
    return "other"


def _classify_response(kind: str, name: str, criteria: str) -> str:
    if kind == "price":
        return "price"
    if "演示" in name or ("演示" in criteria[:60]):
        return "demo"
    if re.search(r"偏离|响应程度|基础服务", name) or ("起评分" in criteria and "扣" in criteria):
        return "compliance"
    evidence = r"证书|业绩|案例|职称|人员|资质|认证|著作权|团队|经验"
    # 名称点明人员/业绩/资质的按证明材料（「技术团队力量保障」看的是证书而非方案）
    if re.search(evidence, name) and "方案" not in name:
        return "evidence"
    if re.search(r"方案|认识|理解|分析|保障|制度|计划|措施|规划|设计", name):
        return "proposal"
    if re.search(evidence + r"|能力|配置", criteria[:80]):
        return "evidence"
    return "proposal"


def _package_label(table: Dict[str, Any]) -> str:
    """从表前文字（取最近一处）或所在章节面包屑识别分包：「（二）包2评审因素」→ 包2"""
    for source in (table.get("context_before", ""), table.get("breadcrumb", "")):
        matches = PACKAGE_RE.findall(source or "")
        if matches:
            numbered, after, before = matches[-1]
            letter = before or (after if re.fullmatch(r"[A-Za-z]", after) else "")
            if letter:
                return letter.upper() + "包"
            raw = numbered or after
            return "包" + CN_DIGITS.get(raw, raw)
    return ""


def _header_columns(cells: List[str]) -> Optional[Dict[str, Optional[int]]]:
    """表头行的列映射；没有分值列时分值写在评分因素里（「项目实施团队（16分）」）"""
    if HEADER_EXCLUDE.search("".join(cells)):
        return None
    points = next((j for j, c in enumerate(cells) if HEADER_POINTS.match(c)), None)
    criteria = next((j for j, c in enumerate(cells) if j != points and HEADER_CRITERIA.search(c)), None)
    if criteria is None:
        criteria = next((j for j, c in enumerate(cells) if j != points and HEADER_CRITERIA_WEAK.search(c)), None)
    if criteria is None:
        return None
    factors = [j for j, c in enumerate(cells) if j not in (points, criteria) and HEADER_CATEGORY.search(c)]
    if points is None and not factors:
        return None
    # 两列因素（「类别 | 评审因素」「评审项 | 评分因素」）：前者是大类，后者是评分项名称
    category = factors[0] if factors else None
    name = factors[1] if len(factors) > 1 else None
    note = next((j for j, c in enumerate(cells) if j > criteria and j != points and HEADER_NOTE.search(c)), None)
    return {"points": points, "criteria": criteria, "category": category, "name": name, "note": note,
            "width": len(cells)}


def _points_from(row: List[str], cols: Dict[str, Optional[int]]) -> Optional[float]:
    if cols["points"] is not None:
        return parse_points_cell(_cell(row, cols["points"]))[1]
    return parse_points_cell(_cell(row, cols["name"] if cols["name"] is not None else cols["category"]))[1]


def _spread_header(cols: Dict[str, Optional[int]], body: List[List[str]]) -> Dict[str, Optional[int]]:
    """
    表头某格横跨多列（「评审因素」盖住大类与因素名两列）时，正文行比表头多出几格：
    逐个假设跨列的表头格，选分值列能读出分数的行最多的那种；跨的是因素列时多出来的一列就是评分项名称
    """
    width = cols["width"]
    lengths = [len(r) for r in body if len(r) > width]
    if not lengths:
        return cols
    wide = max(set(lengths), key=lengths.count)
    extra = wide - width
    rows = [r for r in body if len(r) == wide]
    best, best_score = cols, -1
    for span in range(width):
        moved = {k: (v + extra if isinstance(v, int) and k != "width" and v > span else v) for k, v in cols.items()}
        moved["width"] = wide
        if span == cols["category"] and cols["name"] is None and extra == 1:
            moved["name"] = span + 1
        score = sum(1 for r in rows if _points_from(r, moved) is not None)
        if score > best_score or (score == best_score and span == cols["category"]):
            best, best_score = moved, score
    return best


def _find_header(rows: List[List[str]]) -> Optional[Tuple[int, Dict[str, Optional[int]]]]:
    """在前三行中定位评分表表头，返回（表头行号, 列映射）"""
    for i, row in enumerate(rows[:3]):
        cells = [_clean(c) for c in row]
        if HEADER_EXCLUDE.search("".join(cells)):
            return None
        cols = _header_columns(cells)
        if cols is None:
            continue
        body = rows[i + 1:]
        cols = _spread_header(cols, body)
        if cols["points"] is None:
            # 没有分值列：评分因素里要普遍带着分值，否则不是评分表（如资格审查表）
            full = [r for r in body if len(r) >= cols["width"]]
            if not full or sum(1 for r in full if _points_from(r, cols) is not None) * 2 < len(full):
                continue
        return i, cols
    return None


def _cell(row: List[str], idx: Optional[int]) -> str:
    return row[idx] if idx is not None and idx < len(row) else ""


def extract_scoring_items(tables: List[Dict[str, Any]]) -> List[ScoringItem]:
    """从解析后的表格列表（WordDocumentParser.parse_docx()["tables"]）抽取评分项"""
    scoring_tables = []
    seen_signatures = set()
    for table in tables:
        rows = table.get("rows") or []
        found = _find_header(rows)
        if not found:
            continue
        signature = tuple(tuple(r) for r in rows)
        if signature in seen_signatures:  # 同一评分表在文件中重复出现（如投标文件格式附表）
            continue
        seen_signatures.add(signature)
        scoring_tables.append((table, found))

    per_table: List[Tuple[Dict[str, Any], List[ScoringItem]]] = []
    for table, (header_idx, cols) in scoring_tables:
        table_items = _table_items(table, header_idx, cols)
        if table_items:  # 只有表头的表（如投标文件格式里的空白评分索引表）不算一个分包
            per_table.append((table, table_items))

    items: List[ScoringItem] = []
    multi = len(per_table) > 1
    for t_no, (table, table_items) in enumerate(per_table, 1):
        package = _package_label(table) or (f"包{t_no}" if multi else "")
        prefix = f"pkg{t_no}" if multi else "s"
        for n, it in enumerate(table_items, 1):
            it.package = package
            it.id = f"{prefix}_s{len(items) + n}"
        items.extend(table_items)
    return items


def _table_items(table: Dict[str, Any], header_idx: int, cols: Dict[str, Optional[int]]) -> List[ScoringItem]:
    """一张评分表的评分项（分包与编号由调用方统一填写）"""
    rows = table["rows"]
    keys = table.get("cell_keys") or []
    prev_points_key = prev_name_key = None
    table_items: List[ScoringItem] = []
    for r_idx in range(header_idx + 1, len(rows)):
        row = rows[r_idx]
        rc = _row_columns(row, cols)
        if rc is None:
            # 列数不符（如分节标题行 / 合计行）：不按列映射，避免错位
            prev_points_key = prev_name_key = None
            continue
        points_text = _cell(row, rc["points"])
        name_text = _clean(_cell(row, rc["name"]))
        criteria = _cell(row, rc["criteria"]).strip()
        note = _cell(row, rc["note"]).strip()
        category_raw = _cell(row, rc["category"])
        if re.match(r"^(?:合计|总计|总分|小计)", _clean(category_raw + name_text + points_text)):
            prev_points_key = prev_name_key = None
            continue
        if not (points_text or criteria):
            continue

        row_keys = keys[r_idx] if r_idx < len(keys) else []

        def key_at(idx, row_keys=row_keys):
            return row_keys[idx] if idx is not None and idx < len(row_keys) else None

        # 没有分值列时，分值写在评分项名称（或大类）格里，该格就是"分值单元格"
        points_col = rc["points"] if rc["points"] is not None else (rc["name"] if rc["name"] is not None else rc["category"])
        points_key = key_at(points_col)
        name_key = key_at(rc["name"]) if rc["points"] is not None else None
        last = table_items[-1] if table_items else None
        # 分值单元格与上一行是同一个纵向合并单元格：同一评分项的续行，合并评分标准
        same_points = last is not None and points_key is not None and points_key == prev_points_key
        # 评分项名称格纵向合并、各行分值不同（「方案完整性 4 分」「方案质量 5 分」）：同一评分项，分值相加
        same_name = last is not None and name_key is not None and name_key == prev_name_key
        if same_points or same_name:
            if same_name and not same_points:
                extra = parse_points_cell(points_text)[1]
                if extra is not None:
                    last.points = (last.points or 0) + extra
            if criteria and criteria not in last.criteria:
                last.criteria = f"{last.criteria}\n{criteria}".strip()
            if note and note not in last.note:
                last.note = f"{last.note}\n{note}".strip()
            last.sub_items.extend(_sub_items(criteria))
            prev_points_key = points_key
            continue
        prev_points_key, prev_name_key = points_key, name_key

        category, weight = _split_category(category_raw)
        if rc["points"] is not None:
            name, points = parse_points_cell(points_text)
            name = name_text or name
        else:
            name, points = parse_points_cell(name_text or category_raw)
            if not name_text:
                category = name  # 大类与评分项名称横向合并，分值写在合并格里
        if not name:
            if _classify_kind(category, "") == "price":
                name = parse_points_cell(category)[0] or category  # 「价格部分（10分）」→ 价格部分
            else:
                name = _name_from_criteria(criteria) or f"{category or '评分'}第{len(table_items) + 1}项"
        kind = _classify_kind(category, name)
        table_items.append(ScoringItem(
            id="",
            category=category,
            category_weight=weight,
            kind=kind,
            response_type=_classify_response(kind, name, criteria),
            name=name,
            points=points,
            criteria=criteria,
            note=note,
            sub_items=_sub_items(criteria),
            source="table",
        ))
    return table_items


def _row_columns(row: List[str], cols: Dict[str, Optional[int]]) -> Optional[Dict[str, Optional[int]]]:
    """该行的列映射：大类与评分项名称横向合并的行（「价格部分（10分）」独占两格）少一格，其余列左移"""
    if len(row) >= cols["width"]:
        return cols
    if cols["name"] is not None and len(row) == cols["width"] - 1:
        merged = {k: (v - 1 if isinstance(v, int) and k != "width" and v > cols["name"] else v) for k, v in cols.items()}
        merged["name"] = None
        merged["width"] = cols["width"] - 1
        if _points_from(row, merged) is not None:
            return merged
    return None


def _sub_items(criteria: str) -> List[ScoringSubItem]:
    subs, seen = [], set()
    for m in SUB_ITEM_RE.finditer(criteria or ""):
        name = _clean(m.group(1))
        if name not in seen:
            seen.add(name)
            subs.append(ScoringSubItem(name=name, points=_to_float(m.group(2))))
    return subs


def summarize(items: List[ScoringItem]) -> str:
    """按分包汇总条数与分值合计，合计不为 100 时提示人工核对"""
    if not items:
        return "未在招标文件中识别到评分表（可能为扫描件、图片或非表格格式），请人工录入评分项。"
    by_pkg: Dict[str, List[ScoringItem]] = {}
    for it in items:
        by_pkg.setdefault(it.package, []).append(it)
    parts = []
    for pkg, group in by_pkg.items():
        label = pkg or "评分表"
        unknown = [it.name for it in group if it.points is None]
        total = sum(it.points or 0 for it in group)
        msg = f"{label}：{len(group)} 项，合计 {total:g} 分"
        if unknown:
            msg += f"（{len(unknown)} 项分值未识别：{'、'.join(unknown[:3])}）"
        elif abs(total - 100) > 0.01:
            msg += "（与满分 100 不一致，请人工核对评分表）"
        parts.append(msg)
    return "；".join(parts)


def _is_blank(value: str) -> bool:
    return not value or not value.strip() or value.strip() == NOT_MENTIONED


def apply_to_analysis(analysis: TenderAnalysis18, items: List[ScoringItem]) -> TenderAnalysis18:
    """写入评分细则，并用评分表补全为空的评分相关字段（不覆盖已有结论）"""
    analysis.scoring_items = items
    analysis.scoring_note = summarize(items)
    if not items:
        return analysis

    packages = list(dict.fromkeys(it.package for it in items))
    if not analysis.target_package or analysis.target_package not in packages:
        analysis.target_package = packages[0]
    first = [it for it in items if it.package == packages[0]]

    if _is_blank(analysis.scoring_method):
        analysis.scoring_method = "综合评分法"
    for field, kind in (("price_score_weight", "price"), ("tech_score_weight", "technical"),
                        ("business_score_weight", "business")):
        if not _is_blank(getattr(analysis, field)):
            continue
        group = [it for it in first if it.kind == kind]
        if not group:
            continue
        weight = next((it.category_weight for it in group if it.category_weight is not None), None)
        total = sum(it.points or 0 for it in group)
        detail = "、".join(f"{it.name}{it.points:g}分" for it in group if it.points is not None)
        head = f"{weight:g}%" if weight is not None else f"{total:g}分"
        setattr(analysis, field, f"{head}（{detail}）" if kind != "price" else head)
    return analysis
