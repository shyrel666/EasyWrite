"""
全书一致性检查（F4，IMPROVEMENT_PLAN §9）：把 C1 的事实与资料规则扩展到全部已撰写章节，找出跨章节冲突。

纯规则、离线可用，由导出前检查清单汇总提示（只提示，不阻止导出）：
- 承诺数值与全局事实冲突——单章检查也会报，这里按全书聚合，覆盖从未单独检查过的章节；
- 同一承诺类别在不同章节取值不一致（如 3.1 写 30 分钟响应、5.2 写 2 小时响应）；
- 正文出现示例资料、待核实资料未标注【待核实】、无人员资料支撑的在职/劳动关系/社保断言（复用 C1 的判定）；
- 承诺数值不在全局事实中的条数（待核实口径，只计数不判冲突）。

规则与 C1（section_check）共用同一套实现，判定口径一致；这里只做跨章聚合，不重复单章的要点与篇幅检查。
"""
from typing import Any, Dict, List

from app.models.schemas import OutlineNode, Project
from app.services.assets.asset_manager import asset_manager
from app.services.checker import section_check as sc


def _written_nodes(outline: List[OutlineNode]) -> List[OutlineNode]:
    out: List[OutlineNode] = []
    for n in outline:
        if n.content.strip():
            out.append(n)
        out.extend(_written_nodes(n.children))
    return out


def book_consistency(project: Project) -> Dict[str, Any]:
    """
    全书规则扫描：{sections_checked, fact_conflicts, value_conflicts, asset_issues, commitments_unbacked}。
    冲突条目均为 {detail, section_id}（detail 为可直接展示的完整说明，section_id 供导出前清单链接到章节）。
    """
    nodes = _written_nodes(project.outline)
    snapshot = asset_manager.snapshot()
    fact_qs = sc.fact_quantities(project.facts)
    values: List[Dict[str, Any]] = []      # 每处带类别的承诺数值
    fact_conflicts: List[Dict[str, Any]] = []
    asset_issues: List[Dict[str, Any]] = []
    unbacked = set()

    for node in nodes:
        analysed = sc._mask(node.content, sc.CODE_BLOCK)
        for q in sc.extract_quantities(sc._mask(analysed, sc.PLACEHOLDER)):
            sc._quantity_context(analysed, q, fact_qs)
            topic = q["topic"]
            if topic is None and not q["committed"]:
                continue
            same_topic = [f for f in fact_qs if f["topic"] == topic] if topic else []
            relation = sc.commitment_relation(q, fact_qs)
            if relation == "conflict":
                fact = "；".join(dict.fromkeys(f["fact"] for f in same_topic))
                fact_conflicts.append({
                    "section_id": node.id,
                    "detail": f"{node.title}：{sc.TOPIC_LABELS.get(topic or '', '承诺数值')}与全局事实不一致"
                              f"——正文为「{q['label']}」，全局事实为「{fact}」",
                })
            elif relation != "backed":
                unbacked.add((topic, str(q["value"]), sc.scope_key(q)))
            # 跨章取值比对只看没有事实支撑的取值：与全局事实一致的是正确口径，
            # 偏离事实的按"与全局事实冲突"报，不再重复计"跨章取值不一致"
            if topic and relation == "unbacked":
                values.append(dict(q, section_id=node.id, section_title=node.title))

        issues, _verify = sc._check_assets(analysed, snapshot)
        issues += sc._check_employment(analysed, snapshot)
        asset_issues += [{"section_id": node.id, "detail": f"{node.title}：{i.message}"} for i in issues]

    # 同一类别在多个取值之间摇摆：按 same_value 聚类，超过一类即冲突
    value_conflicts: List[Dict[str, Any]] = []
    by_topic: Dict[tuple, List[Dict[str, Any]]] = {}
    for v in values:
        by_topic.setdefault((v["topic"], v.get("dimension"), sc.scope_key(v)), []).append(v)
    for (topic, _dimension, _scope), group in by_topic.items():
        clusters: List[List[Dict[str, Any]]] = []
        for v in group:
            for c in clusters:
                if sc.same_value(c[0], v):
                    c.append(v)
                    break
            else:
                clusters.append([v])
        if len(clusters) > 1 and len({v["section_id"] for v in group}) > 1:
            where = "；".join(f"「{c[0]['label']}」（{c[0]['section_title']}）" for c in clusters)
            value_conflicts.append({
                "section_id": group[0]["section_id"],
                "detail": f"{sc.TOPIC_LABELS.get(topic, topic)}在不同章节取值不一致：{where}",
            })

    return {"sections_checked": len(nodes), "fact_conflicts": fact_conflicts,
            "value_conflicts": value_conflicts, "asset_issues": asset_issues,
            "commitments_unbacked": len(unbacked)}
