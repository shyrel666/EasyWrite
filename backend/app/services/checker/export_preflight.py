"""
导出前检查清单（纯规则，离线可用；只提示，不阻止导出）：
未撰写章节、剩余【待填写】/【待核实】占位、用了待核实资料的章节、证明材料缺口、未校审章节、
最近一次废标核查中未响应的红线条款、偏离表中仍为"待生成"的条目。
"""
import re
from typing import Any, Dict, List, Optional

from app.models.schemas import OutlineNode, Project
from app.services.assets.asset_manager import asset_manager
from app.services.assets.evidence import evidence_report
from app.services.assets.material_check import COMPLETE

PLACEHOLDER = re.compile(r"【(?:待填写|待核实)[^】]{0,60}】")
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


def _placeholders(nodes: List[OutlineNode]) -> Dict[str, Any]:
    details, total = [], 0
    for n in nodes:
        found = PLACEHOLDER.findall(n.content)
        if found:
            total += len(found)
            samples = list(dict.fromkeys(found))[:3]
            details.append(_detail(n.title, f"{len(found)} 处：{'、'.join(samples)}", n.id))
    return _check("placeholders", "剩余【待填写】/【待核实】占位", WARN if total else OK,
                  f"{len(details)} 个章节共 {total} 处占位，导出前补齐或确认" if total else "正文中没有占位",
                  details, count=total)


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


def _material(project: Project) -> Dict[str, Any]:
    report = evidence_report(project)
    if not report["total"]:
        return _check("material", "证明材料缺口", OK, "没有需要证明材料的评分项")
    gaps = []
    for it in report["items"]:
        if it["status"] == COMPLETE:
            continue
        problems = [p["message"] for c in it["links"] for p in c["problems"]]
        gaps.append(_detail(it["name"], it["status"] + (f"：{problems[0]}" if problems else "")))
    return _check("material", "证明材料缺口", WARN if gaps else OK,
                  f"{len(gaps)}/{report['total']} 个评分项证明材料不齐备" if gaps
                  else f"{report['total']} 个评分项证明材料齐备", gaps)


def _unreviewed(leaves: List[OutlineNode]) -> Dict[str, Any]:
    pending = [_detail(n.title, section_id=n.id) for n in leaves if n.content.strip() and n.status != "reviewed"]
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
               if it.response_status == "待生成"]
    return _check("deviation_pending", "偏离表待生成条目", WARN if pending else OK,
                  f"{len(pending)} 条技术条款尚未应答" if pending else "偏离表条目均已应答", pending)


def build_preflight(project: Project, latest_compliance: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    nodes = _flatten(project.outline)
    leaves = [n for n in nodes if not n.children]
    checks = [
        _unwritten(leaves),
        _placeholders(nodes),
        _unverified_assets(nodes),
        _material(project),
        _unreviewed(leaves),
        _redlines(project, latest_compliance),
        _deviation_pending(project),
    ]
    attention = sum(1 for c in checks if c["status"] != OK)
    return {"checks": checks, "attention": attention, "ready": attention == 0}
