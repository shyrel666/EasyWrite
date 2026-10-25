"""
评分点覆盖核查（规则快扫，替代原先写死的"评分点对标度"分数）。

对本次投标分包的每个评分项（报价除外）检查：
1. 是否有章节承接（大纲节点 scoring_item_ids）——无则"未承接"
2. 承接章节是否已有正文——无则"未撰写"
3. 评分子项 / 评分标准列举的要点是否在已撰写的章节标题或正文中出现——缺则"要点缺失"
4. 方案类正文是否达到字数预算的 30%——不足则"篇幅不足"
覆盖率按分值加权，只反映"有没有写到"，不评判写得好不好（后者需评审或大模型深度审查）。
证明材料（material）是另一项结论：由调用方传入 {评分项ID: {status, notes, assets}}，原样附在结果上，
不影响文字覆盖率。
"""
import re
from typing import Any, Dict, List, Optional

from app.models.schemas import OutlineNode, ScoringCoverage, TenderAnalysis18
from app.services.generator import rubric_planner as rp
from app.services.text_stats import body_chars

MIN_LENGTH_RATIO = 0.3
FILLER = re.compile(r"方案|措施|计划|情况|内容|的|及|与|和|等|对")
# 分隔标点（顿号、斜杠、句读等）：要点与正文两侧同样去除后再比对。
# "软、硬件的运维方案"去顿号后核心词为"软件运维"，正文分别写出"软件运维方案""硬件运维方案"才能对上；
# 否则顿号会进入二字片段（"软、""、硬"），正文里永远凑不齐 70%，把已写到的要点误判为缺失
SEP = re.compile(r"[\s、，,。．.；;：:/／()（）【】\[\]·]")


def _normalize(text: str) -> str:
    return FILLER.sub("", SEP.sub("", text))


def _subtree(node: OutlineNode) -> List[OutlineNode]:
    out = [node]
    for c in node.children:
        out.extend(_subtree(c))
    return out


def _flatten(nodes: List[OutlineNode]) -> List[OutlineNode]:
    out: List[OutlineNode] = []
    for n in nodes:
        out.extend(_subtree(n))
    return out


def _bigrams(text: str) -> set:
    return {text[i:i + 2] for i in range(len(text) - 1)}


def point_overlap(point: str, text: str) -> float:
    """要点与文本的贴近度：原文或归一化核心词整体出现记 1，否则为核心词二字片段在归一化文本中出现的比例。

    归一化在要点与文本两侧同样进行（去空白、分隔标点与虚词），容忍“软、硬件”对“软硬件”、
    “风险的防范和应对”对“风险防范与应对”这类标点与虚词差异；阈值仍是 0.7。"""
    compact = re.sub(r"\s+", "", text)
    p = re.sub(r"\s+", "", point)
    if not p or p in compact:
        return 1.0
    core = _normalize(point)
    norm_text = _normalize(text)
    if len(core) >= 2 and core in norm_text:
        return 1.0
    grams = _bigrams(core)
    if len(grams) < 2:
        return 0.0
    return sum(1 for g in grams if g in norm_text) / len(grams)


def is_mentioned(point: str, text: str) -> bool:
    """要点是否在文本中出现：原文 / 去虚词核心词命中，或核心词 70% 以上的二字片段命中（容忍语序改写）"""
    return point_overlap(point, text) >= 0.7


def check_scoring_coverage(
    outline: List[OutlineNode], ta: Optional[TenderAnalysis18],
    material: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[ScoringCoverage]:
    items = rp.target_items(ta)
    material = material or {}
    flat = _flatten(outline)
    results: List[ScoringCoverage] = []
    for it in items:
        mapped = [n for n in flat if it.id in n.scoring_item_ids]
        # 只取最具体的承接节点：章节与其子节点同时承接时以子节点为准（共享章节的字数预算不重复计入）
        mapped = [n for n in mapped
                  if not any(d is not n and it.id in d.scoring_item_ids for d in _subtree(n))]
        sections: Dict[str, OutlineNode] = {}
        for n in mapped:
            for s in _subtree(n):
                sections[s.id] = s
        written = [s for s in sections.values() if s.content.strip()]
        text = "\n".join(f"{s.title}\n{s.content}" for s in written)
        words = sum(body_chars(s.content) for s in written)
        leaves = [s for s in sections.values() if not s.children]
        budget = sum(s.word_budget or 0 for s in leaves) or sum(n.word_budget or 0 for n in mapped)

        points = [s.name for s in it.sub_items] or rp.content_points(it.criteria)
        missing: List[str] = []
        if not mapped:
            status, coverage = "未承接", 0.0
        elif not written:
            status, coverage = "未撰写", 0.0
        else:
            missing = [p for p in points if not is_mentioned(p, text)]
            coverage = 1 - len(missing) / len(points) if points else 1.0
            # 篇幅只核对已撰写小节的预算（未撰写小节已体现为要点缺失，不重复扣分）
            written_budget = sum(s.word_budget or 0 for s in written if not s.children) or budget
            length_ok = (it.response_type != "proposal" or not written_budget
                         or words >= written_budget * MIN_LENGTH_RATIO)
            if not length_ok:
                coverage *= 0.5
            status = "要点缺失" if missing else ("篇幅不足" if not length_ok else "已覆盖")
        mat = material.get(it.id) or {}
        results.append(ScoringCoverage(
            item_id=it.id, name=it.name, points=it.points, response_type=it.response_type,
            status=status, coverage=round(coverage, 3),
            section_titles=[n.title for n in mapped], missing_points=missing,
            word_count=words, word_budget=budget or None,
            material_status=mat.get("status"), material_notes=mat.get("notes", []),
            material_assets=mat.get("assets", []),
        ))
    return results


def coverage_rate(results: List[ScoringCoverage]) -> Optional[float]:
    """按分值加权的覆盖率（0~1）；分值未识别的评分项按 1 分计"""
    if not results:
        return None
    total = sum(r.points or 1 for r in results)
    return sum((r.points or 1) * r.coverage for r in results) / total
