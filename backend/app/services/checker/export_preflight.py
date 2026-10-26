"""
导出前检查清单（纯规则，离线可用；只提示，不阻止导出）：
未撰写章节、剩余【待填写】/【待核实】占位、用了待核实资料的章节、证明材料缺口、未校审章节、
最近一次废标核查中未响应的红线条款、偏离表中仍为"待生成"的条目。
"""
from typing import Any, Dict, List, Optional

from app.models.schemas import OutlineNode, Project
from app.services.assets.asset_manager import asset_manager
from app.services.assets.evidence import evidence_report
from app.services.assets.material_check import COMPLETE
from app.services.checker.book_consistency import book_consistency
from app.services.checker.placeholders import PLACEHOLDER
from app.services.checker.verification import project_verification, verification_counts

MAX_DETAILS = 50
OK, WARN, UNKNOWN = "ok", "warn", "unknown"


def _flatten(nodes: List[OutlineNode]) -> List[OutlineNode]:
    out: List[OutlineNode] = []
    for n in nodes:
        out.append(n)
        out.extend(_flatten(n.children))
    return out


def _detail(title: str, detail: str = "", section_id: str = "") -> Dict[str, str]:
    return {"title": title, "detail": detail, "section_id": section_id}


def _check(key: str, title: str, status: str, message: str, details: Optional[List[dict]] = None,
           count: Optional[int] = None) -> Dict[str, Any]:
    details = details or []
    return {"key": key, "title": title, "status": status, "message": message,
            "count": len(details) if count is None else count, "details": details[:MAX_DETAILS]}


def _unwritten(leaves: List[OutlineNode]) -> Dict[str, Any]:
    if not leaves:
        return _check("unwritten", "未撰写的章节", WARN, "尚未规划大纲")
    empty = [_detail(n.title, section_id=n.id) for n in leaves if not n.content.strip()]
    return _check("unwritten", "未撰写的章节", WARN if empty else OK,
                  f"{len(empty)} 个章节尚未撰写" if empty else "全部章节都已有正文", empty)


def _placeholders(nodes: List[OutlineNode], verification) -> Dict[str, Any]:
    details, total = [], 0
    by_section = {}
    item_keys = set()
    node_map = {n.id: n for n in nodes}
    for v in verification:
        for o in v.occurrences:
            node = node_map.get(o.section_id)
            if node is None or o.start is None or o.end is None:
                continue
            if PLACEHOLDER.search(node.content[o.start:o.end]):
                by_section.setdefault(node.id, []).append(node.content[o.start:o.end])
                item_keys.add(v.stable_key)
    for n in nodes:
        found = by_section.get(n.id, [])
        if found:
            total += len(found)
            samples = list(dict.fromkeys(found))[:3]
            details.append(_detail(n.title, f"{len(found)} 处：{'、'.join(samples)}", n.id))
    result = _check("placeholders", "剩余【待填写】/【待核实】占位", WARN if total else OK,
                  f"{len(details)} 个章节共 {total} 处占位，导出前补齐或确认" if total else "正文中没有占位",
                  details, count=total)
    return {**result, "item_count": len(item_keys), "occurrence_count": total}


def _unverified_assets(nodes: List[OutlineNode]) -> Dict[str, Any]:
    """撰写时用了待核实资料、且该资料至今仍未确认（或已删除）的章节"""
    details = []
    for n in nodes:
        names = []
        for ref in n.last_refs:
            if ref.get("ref_type") != "asset" or ref.get("status") != "unverified":
                continue
            current = asset_manager.get_asset(ref.get("kind", ""), ref.get("asset_id", ""))
            if current is None:
                names.append(f"{ref.get('name', '')}（资料已删除）")
            elif current.get("status") == "unverified":
                names.append(ref.get("name", ""))
        if names:
            details.append(_detail(n.title, "、".join(names), n.id))
    return _check("unverified_assets", "用了待核实资料的章节", WARN if details else OK,
                  f"{len(details)} 个章节引用的资料仍待核实" if details else "没有章节使用待核实资料", details)


def _book_consistency(project: Project) -> Dict[str, Any]:
    """F4：全书一致性——事实冲突、跨章取值、资料标注（规则离线，只提示不阻止导出）"""
    report = book_consistency(project)
    conflicts = report["fact_conflicts"] + report["value_conflicts"] + report["asset_issues"]
    note = (f"；另有 {report['commitments_unbacked']} 个承诺数值不在全局事实中，导出前核实"
            if report["commitments_unbacked"] else "")
    if not conflicts:
        return _check("book_consistency", "全书一致性", OK,
                      f"{report['sections_checked']} 个已撰写章节未发现跨章冲突{note}")
    kinds = [f"{label} {len(report[key])} {'处' if key != 'value_conflicts' else '组'}"
             for key, label in (("fact_conflicts", "与全局事实冲突"), ("value_conflicts", "跨章取值不一致"),
                                ("asset_issues", "资料问题")) if report[key]]
    details = [_detail(c["detail"], section_id=c["section_id"]) for c in conflicts]
    return _check("book_consistency", "全书一致性", WARN, "、".join(kinds) + note, details)


def _material(project: Project) -> Dict[str, Any]:
    report = evidence_report(project)
    if not report["total"]:
        return _check("material", "证明材料缺口", OK, "没有需要证明材料的评分项")
    gaps = []
    for it in report["items"]:
        if it["status"] == COMPLETE:
            continue
        problems = [p["message"] for c in it["links"] for p in c["problems"]]
        if it["missing_links"]:
            problems.append(f"{len(it['missing_links'])} 条关联资料已被删除，请重新关联")
        gaps.append(_detail(it["name"], it["status"] + (f"：{'；'.join(problems)}" if problems else "")))
    return _check("material", "证明材料缺口", WARN if gaps else OK,
                  f"{len(gaps)}/{report['total']} 个评分项证明材料不齐备" if gaps
                  else f"{report['total']} 个评分项证明材料齐备", gaps)


def _unreviewed(nodes: List[OutlineNode]) -> Dict[str, Any]:
    pending = [_detail(n.title, section_id=n.id) for n in nodes if n.content.strip()
               and (n.status != "reviewed" or n.reviewed_revision != n.revision)]
    return _check("unreviewed", "未校审的章节", WARN if pending else OK,
                  f"{len(pending)} 个已撰写章节尚未人工校审" if pending else "已撰写的章节均已校审", pending)


def _redlines(project: Project, latest: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    title = "废标红线核查"
    if not latest:
        return _check("redlines", title, UNKNOWN, "尚未执行废标红线核查，建议导出前在「质检与合规」执行")
    if latest.get("status") != "completed" or not latest.get("result"):
        state = {"failed": "失败", "interrupted": "因服务重启中断", "cancelled": "已取消"}.get(latest.get("status"), "尚未完成")
        return _check("redlines", title, UNKNOWN, f"最近一次核查{state}，请重新执行")
    result = latest["result"]
    total, satisfied = result.get("total_star_items", 0), result.get("satisfied_star_items", 0)
    risks = [_detail(r.get("item", ""), r.get("reason", "")) for r in result.get("risk_items", [])]
    checked_at = latest.get("updated_at", "")
    stale = "；核查后项目内容有更新，建议重新核查" if checked_at and project.updated_at > checked_at else ""
    mode = "关键词快扫" if result.get("mode") == "rules" else "模型证据核查"
    if risks or satisfied < total:
        message = f"{checked_at} {mode}：{total} 项红线条款中 {total - satisfied} 项未明确响应，风险 {len(risks)} 项{stale}"
        return _check("redlines", title, WARN, message, risks, count=max(total - satisfied, len(risks)))
    return _check("redlines", title, UNKNOWN if stale else OK,
                  f"{checked_at} {mode}：{total} 项红线条款均已响应{stale}")


def _deviation_pending(project: Project) -> Dict[str, Any]:
    if not project.deviation_matrix:
        return _check("deviation_pending", "偏离表待生成条目", OK, "未建立技术偏离表")
    pending = [_detail(f"第 {it.index} 条", it.clause_title[:80]) for it in project.deviation_matrix
               if it.response_status not in ("完全满足", "正偏离", "负偏离")]
    return _check("deviation_pending", "偏离表未完成核实条目", WARN if pending else OK,
                  f"{len(pending)} 条技术条款尚未应答或待核实" if pending else "偏离表条目均已应答", pending)


def build_preflight(project: Project, latest_compliance: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    nodes = _flatten(project.outline)
    leaves = [n for n in nodes if not n.children]
    verification = project_verification(project)
    checks = [
        _unwritten(leaves),
        _placeholders(nodes, verification),
        _unverified_assets(nodes),
        _book_consistency(project),
        _material(project),
        _unreviewed(nodes),
        _redlines(project, latest_compliance),
        _deviation_pending(project),
    ]
    attention = sum(1 for c in checks if c["status"] != OK)
    totals = verification_counts(verification)
    if totals["item_count"]:
        checks[1]["message"] += f"；全书待核实 {totals['item_count']} 项 / {totals['occurrence_count']} 处"
    return {"checks": checks, "attention": attention, "ready": attention == 0,
            "verification": [v.model_dump() for v in verification], "verification_counts": totals}
