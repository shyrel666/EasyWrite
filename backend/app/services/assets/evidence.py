"""
评分项与企业资料的关联（证明材料是否齐备）。

- 关联存放在项目的 evidence_links：{评分项ID: ["kind:资料ID", …]}，只由用户确认保存，不自动关联
- 关联建议（suggest_links）：证明材料类评分项按评分标准文本与资料名称、证书名称的相似度给出候选，
  证书编号类关键词（ISO27001、ISO20000…）必须原样出现才算匹配，避免"管理体系认证"之类的通用词误配
- 材料状态（evidence_report）：逐个关联资料做 check_material，取最严重的状态；没有关联为"未关联"
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from app.models.schemas import MaterialCheck, Project, ScoringItem
from app.services.assets.asset_manager import asset_manager
from app.services.assets.matching import best_match
from app.services.assets.material_check import (
    MATERIAL_KINDS, NOT_LINKED, asset_name, bid_deadline, blocking_filter, check_material, worst_status,
)
from app.services.generator import rubric_planner as rp

KIND_LABELS = {"qualifications": "资质", "personnel": "人员", "cases": "业绩"}
# 评分项提到这些词时，业绩资料按类别建议（"每提供一个类似业绩得 x 分"通常不点名具体项目）
CASE_HINT = re.compile(r"业绩|项目经验|案例|类似项目|同类项目")
MAX_SUGGESTIONS = 8


def make_key(kind: str, asset_id: str) -> str:
    return f"{kind}:{asset_id}"


def parse_key(key: str) -> Tuple[str, str]:
    kind, _, asset_id = (key or "").partition(":")
    return kind, asset_id


def needs_material(item: ScoringItem) -> bool:
    return item.response_type == "evidence"


def _item_text(item: ScoringItem) -> str:
    return f"{item.name} {item.criteria} {item.note}"


def _candidate(kind: str, asset: Dict[str, Any], item: ScoringItem, project_name: str) -> Optional[Dict[str, Any]]:
    text = _item_text(item)
    if kind == "qualifications":
        score, hit = best_match([asset.get("name", "")], text)
        if score < 0.6:
            return None
        reason = f"资质名称与评分标准匹配：{hit}"
    elif kind == "personnel":
        phrases = list(asset.get("certificates") or []) + [asset.get("professional_title", "")]
        score, hit = best_match(phrases, text)
        if score < 0.8:
            return None
        reason = f"持有评分标准提到的证书/职称：{hit}"
    else:
        if not CASE_HINT.search(text):
            return None
        phrases = [asset.get("project_name", ""), asset.get("contract_category", "")] + list(asset.get("key_deliverables") or [])
        similarity, hit = best_match(phrases, f"{text} {project_name}")
        score = 0.5 + 0.5 * similarity
        reason = "业绩类评分项" + (f"，关键词匹配：{hit}" if similarity >= 0.5 else "（请人工确认是否属于类似项目）")
    return {
        "key": make_key(kind, asset["id"]),
        "kind": kind,
        "asset_id": asset["id"],
        "name": asset_name(kind, asset),
        "asset_status": asset.get("status", "confirmed"),
        "score": round(min(score, 1.0), 3),
        "reason": reason,
    }


def _usable_assets() -> Dict[str, List[Dict[str, Any]]]:
    """可作为证明材料的资料（排除预设示例）"""
    return {
        kind: [a for a in (asset_manager.get_asset(kind, x["id"]) for x in getattr(asset_manager, kind))
               if a and a.get("status") != "example"]
        for kind in MATERIAL_KINDS
    }


def suggest_links(project: Project) -> Dict[str, List[Dict[str, Any]]]:
    """证明材料类评分项的关联建议（按相似度降序，已关联的条目标记 linked），只供用户确认，不写入项目"""
    ta = project.tender_analysis
    assets = _usable_assets()
    project_name = ta.project_name if ta else project.name
    out: Dict[str, List[Dict[str, Any]]] = {}
    for item in rp.target_items(ta):
        if not needs_material(item):
            continue
        linked = set(project.evidence_links.get(item.id, []))
        candidates = [c for kind in MATERIAL_KINDS for a in assets[kind]
                      if (c := _candidate(kind, a, item, project_name))]
        candidates.sort(key=lambda c: -c["score"])
        for c in candidates:
            c["linked"] = c["key"] in linked
        out[item.id] = candidates[:MAX_SUGGESTIONS]
    return out


def generation_filter(project: Project):
    """撰写与偏离表应答用的排除规则：证书过期（含投标截止日前到期）、所属主体与投标人不一致的资料不进入提示词"""
    deadline, _ = bid_deadline(project.tender_analysis)
    return blocking_filter(project.facts, deadline)


def resolve_links(keys: List[str], snapshot=None) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[str]]:
    """关联键 → (资料列表, 已不存在的键)"""
    found, missing = [], []
    for key in keys:
        kind, asset_id = parse_key(key)
        if snapshot is None:
            asset = asset_manager.get_asset(kind, asset_id) if kind in MATERIAL_KINDS else None
        else:
            asset = next((a for a in snapshot.get(kind, []) if a.get("id") == asset_id), None)
        if asset is None:
            missing.append(key)
        else:
            found.append((kind, asset))
    return found, missing


def evidence_report(project: Project) -> Dict[str, Any]:
    """
    评分项证明材料状态：证明材料类评分项与已关联资料的评分项逐项给出
    status（齐备 / 示例资料 / 过期 / 主体不符 / 缺附件 / 待核实 / 未关联）与每条关联资料的检查结果。
    """
    ta = project.tender_analysis
    deadline, source = bid_deadline(ta)
    items = []
    for item in rp.target_items(ta):
        keys = project.evidence_links.get(item.id, [])
        if not needs_material(item) and not keys:
            continue
        found, missing = resolve_links(keys)
        checks: List[MaterialCheck] = [check_material(kind, a, project.facts, deadline) for kind, a in found]
        items.append({
            "item_id": item.id,
            "name": item.name,
            "points": item.points,
            "response_type": item.response_type,
            "criteria": item.criteria,
            "note": item.note,
            "needs_material": needs_material(item),
            "status": worst_status(c.status for c in checks) if checks else NOT_LINKED,
            "links": [c.model_dump() for c in checks],
            "missing_links": missing,
        })
    complete = sum(1 for it in items if it["status"] == "齐备")
    return {
        "reference_date": deadline.isoformat(),
        "reference_source": source,
        "company_name": project.facts.company_name,
        "total": len(items),
        "complete": complete,
        "items": items,
    }


def material_by_item(report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """evidence_report → 评分覆盖核查用的 {评分项ID: {status, notes, assets}}"""
    out = {}
    for it in report["items"]:
        notes = [f"{c['name']}：{p['message']}" for c in it["links"] for p in c["problems"]]
        if it["missing_links"]:
            notes.append(f"{len(it['missing_links'])} 条关联资料已被删除")
        out[it["item_id"]] = {"status": it["status"], "notes": notes, "assets": [c["name"] for c in it["links"]]}
    return out
