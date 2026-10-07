"""
评分细则驱动的大纲规划（确定性部分）。

评审专家按评分标准逐点打分：章节结构与评分点一一对应，专家才能快速"找到分"。因此：
- 需撰写方案的评分项（proposal）各自独立成章；子章节取评分子项（「1.现状及需求分析（10分）」）
  或评分标准中「内容包括：①…②…」列出的要点——标题即评分点
- 逐条响应类（起评分扣分制）合为一章，走点对点应答表（偏离表回填）
- 现场演示类合为一章（演示脚本与功能点准备）
- 证明材料类（人员/业绩/资质）合为一章，走模板填充（企业资产中台）
- 报价不进入技术标

大模型规划时同样以此为准绳：漏掉的评分项由 ensure_coverage 自动补章，保证每个评分项都有承接章节。
"""
import re
from typing import Dict, List, Optional, Tuple

from app.models.schemas import OutlineNode, ScoringItem, TenderAnalysis18

# 进入技术标正文的应答方式（报价除外）
CHAPTER_TYPES = ("proposal", "compliance", "demo", "evidence")
# 字数权重系数：分值相同，方案论述需要的篇幅远大于演示脚本/应答表/证书清单（后者靠现场或材料得分）
TYPE_WORD_FACTOR = {"proposal": 1.0, "demo": 0.3, "compliance": 0.25, "evidence": 0.2}
GROUP_TITLES = {
    "compliance": ("技术需求逐条响应", "point_to_point"),
    "demo": ("现场演示方案", "ai_generate"),
    "evidence": ("商务资信与证明材料", "template_fill"),
}
CN_DIGITS = "一二三四五六七八九十"
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"


def cn_num(n: int) -> str:
    if n <= 10:
        return CN_DIGITS[n - 1]
    if n < 20:
        return "十" + CN_DIGITS[n - 11]
    tens, ones = divmod(n, 10)
    return CN_DIGITS[tens - 1] + "十" + (CN_DIGITS[ones - 1] if ones else "")


def fmt_points(points: Optional[float]) -> str:
    return f"{points:g}分" if points is not None else "分值未识别"


def target_items(ta: Optional[TenderAnalysis18]) -> List[ScoringItem]:
    """本次投标分包中需在技术标内承接的评分项（不含报价）"""
    if not ta or not ta.scoring_items:
        return []
    packages = [it.package for it in ta.scoring_items]
    pkg = ta.target_package if ta.target_package in packages else packages[0]
    return [it for it in ta.scoring_items if it.package == pkg and it.response_type in CHAPTER_TYPES]


def content_points(criteria: str, limit: int = 8) -> List[str]:
    """
    提取评分标准中列举的内容要点：
    「内容包括： 1.审计作业场景升级优化方案； 2.业务板块升级优化方案。 上述内容……」→ [审计作业场景…, 业务板块…]
    「内容包含： ①工作进度计划； ②详细工作内容的阐述……」/「内容包括： 运维流程； 运维体系……」同理
    """
    text = criteria or ""
    m = re.search(r"(?:内容包括|内容包含|主要包括|包括以下内容|包括|包含)[：:]", text)
    if m:
        segment = text[m.end():]
        end = re.search(r"上述|以上内容|不存在瑕疵|方案内容|。\s*(?![①-⑩\d（(])", segment)
        if end:
            segment = segment[:end.start()]
    else:
        # 无「包括：」时：顶层编号且自带"演示/方案"的功能点（演示评分项的功能清单）
        found = re.findall(r"(?:^|\s)\d{1,2}[.、．]\s*([^\s，。；;（(]{2,24}?(?:演示|方案))", text)
        if found:
            return _dedupe(found, limit)
        # 或行内枚举：「包括工单管理、任务管理、人员管理、知识库等功能」
        inline = re.search(r"包括([^。：:；;，,]{4,80}?)等", text)
        parts = [p.strip() for p in inline.group(1).split("、")] if inline else []
        return _dedupe([p for p in parts if 2 <= len(p) <= 20], limit) if len(parts) >= 2 else []
    parts = re.split(r"[；;]|(?=[①-⑩])|\s+(?=\d{1,2}[.、．）)])", segment)
    cleaned = []
    for p in parts:
        p = re.sub(r"^\s*(?:[①-⑩]|\d{1,2}[.、．）)]|[（(]\d{1,2}[）)])\s*", "", p)
        p = re.sub(r"等$", "", p.strip(" 。；;，,、　"))
        if 2 <= len(p) <= 40:
            cleaned.append(p)
    return _dedupe(cleaned, limit)


def _dedupe(values: List[str], limit: int) -> List[str]:
    out = []
    for v in values:
        if v not in out:
            out.append(v)
    return out[:limit]


def item_requirements(it: ScoringItem) -> List[str]:
    reqs = [f"评分项：{it.name}（{fmt_points(it.points)}）"]
    points = [s.name for s in it.sub_items] or content_points(it.criteria)
    reqs += [f"须覆盖：{p}" for p in points]
    if it.response_type == "evidence" and it.note:
        reqs.append(f"证明材料：{it.note[:80]}")
    if "瑕疵" in (it.criteria + it.note):
        reqs.append("评审按“瑕疵”扣分：内容须完整、无漏项、表述准确、具有针对性")
    return reqs[:8]


def _chapter(title: str, items: List[ScoringItem], mode: str) -> Dict:
    reqs: List[str] = []
    for it in items:
        reqs += item_requirements(it) if len(items) == 1 else [f"评分项：{it.name}（{fmt_points(it.points)}）"]
    return {"title": title, "requirements": reqs[:8], "scoring_item_ids": [it.id for it in items], "content_mode": mode}


def rubric_chapters(items: List[ScoringItem]) -> List[Dict]:
    """确定性一级章节：方案类逐项成章，其余按应答方式归并"""
    chapters = [_chapter(it.name, [it], "ai_generate") for it in items if it.response_type == "proposal"]
    for rtype, (title, mode) in GROUP_TITLES.items():
        group = [it for it in items if it.response_type == rtype]
        if group:
            chapters.append(_chapter(title if len(group) > 1 or rtype != "demo" else group[0].name, group, mode))
    return number_chapters(chapters)


def number_chapters(chapters: List[Dict]) -> List[Dict]:
    for i, ch in enumerate(chapters, 1):
        bare = re.sub(r"^第[一二三四五六七八九十百\d]+章\s*", "", ch["title"]).strip()
        ch["title"] = f"第{cn_num(i)}章 {bare}"
    return chapters


def ensure_coverage(chapters: List[Dict], items: List[ScoringItem]) -> Tuple[List[Dict], List[str]]:
    """校正大模型章节：剔除未知评分项 ID，为未被任何章节承接的评分项补章；返回（章节, 补章的评分项名）"""
    known = {it.id for it in items}
    covered = set()
    for ch in chapters:
        ch["scoring_item_ids"] = [x for x in ch.get("scoring_item_ids", []) if x in known]
        covered.update(ch["scoring_item_ids"])
    missing = [it for it in items if it.id not in covered]
    if missing:
        chapters = chapters + rubric_chapters(missing)
    return number_chapters(chapters), [it.name for it in missing]


def rubric_prompt(items: List[ScoringItem]) -> str:
    """供大模型规划的评分细则清单（含 ID，要求章节回填 scoring_item_ids）"""
    if not items:
        return ""
    labels = {"proposal": "撰写方案", "compliance": "逐条响应", "demo": "现场演示", "evidence": "证明材料"}
    lines = []
    for it in items:
        points = "、".join(s.name for s in it.sub_items) or "、".join(content_points(it.criteria)[:6])
        lines.append(
            f"- [{it.id}] {it.name}（{fmt_points(it.points)}，{labels.get(it.response_type, it.response_type)}）"
            + (f"：须覆盖 {points}" if points else "")
        )
    return "【评分细则（每个评分项都必须由章节承接，在 scoring_item_ids 中回填其 ID）】：\n" + "\n".join(lines)


def chapter_children(index: int, chapter: Dict, items_by_id: Dict[str, ScoringItem]) -> Tuple[List[OutlineNode], Dict[str, float]]:
    """确定性二级章节：评分子项 > 评分标准列举要点 > 按评分项拆分；返回（子节点, 子节点字数权重）"""
    items = [items_by_id[x] for x in chapter.get("scoring_item_ids", []) if x in items_by_id]
    children: List[OutlineNode] = []
    weights: Dict[str, float] = {}

    def add(title: str, reqs: List[str], mode: str = "ai_generate", weight: float = 1.0, ids=()):
        j = len(children) + 1
        node = OutlineNode(
            id=f"sec_{index}_{j}", title=f"{index}.{j} {title}", level=2,
            requirements=reqs[:6], content_mode=mode, scoring_item_ids=list(ids),
        )
        children.append(node)
        weights[node.id] = weight

    if len(items) == 1 and items[0].response_type in ("proposal", "demo"):
        it = items[0]
        parent = f"所属评分项：{it.name}（{fmt_points(it.points)}）"
        if it.sub_items:
            for s in it.sub_items:
                add(s.name, [f"评分子项：{s.name}（{fmt_points(s.points)}）", parent], weight=s.points or 1.0, ids=[it.id])
        else:
            points = content_points(it.criteria)
            if len(points) >= 2:
                for p in points:
                    add(p, [f"评分要点：{p}", parent], ids=[it.id])
            else:  # 无可拆分要点：整章作为单一可写小节，不套用与评分项无关的通用模板
                add(it.name, item_requirements(it), ids=[it.id])
    elif items:
        for it in items:
            mode = {"compliance": "point_to_point", "evidence": "template_fill"}.get(it.response_type, "ai_generate")
            title = f"{it.name}响应" if it.response_type == "compliance" else it.name
            add(title, item_requirements(it), mode, weight=it.points or 1.0, ids=[it.id])
    return children, weights


def chapter_word_weight(node: OutlineNode, items_by_id: Dict[str, ScoringItem],
                        shares: Optional[Dict[str, int]] = None) -> float:
    """章节字数权重 = Σ 承接评分项分值 × 应答方式系数 ÷ 该评分项被几个章节分担（避免一项拆成多章时重复计分）"""
    shares = shares or {}
    return sum(
        (items_by_id[x].points or 0) * TYPE_WORD_FACTOR.get(items_by_id[x].response_type, 1.0) / max(shares.get(x, 1), 1)
        for x in node.scoring_item_ids if x in items_by_id
    )
