"""评分项证明材料路由：材料状态、关联建议、用户确认关联"""
from typing import List

from fastapi import APIRouter, Body, HTTPException

from app.models.schemas import Project
from app.services.assets.evidence import evidence_report, parse_key, resolve_links, suggest_links
from app.services.assets.material_check import MATERIAL_KINDS
from app.services.generator import rubric_planner as rp
from app.services.project_store import project_store

router = APIRouter(tags=["证明材料"])


def _get_project(project_id: str) -> Project:
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


@router.get("/project/{project_id}/evidence", summary="评分项证明材料状态（齐备 / 缺附件 / 过期 / 主体不符 / 待核实 / 未关联）")
def get_evidence(project_id: str):
    return evidence_report(_get_project(project_id))


@router.get("/project/{project_id}/evidence/suggestions", summary="证明材料类评分项的关联建议（按名称相似度，须用户确认后保存）")
def get_evidence_suggestions(project_id: str):
    return {"suggestions": suggest_links(_get_project(project_id))}


@router.put("/project/{project_id}/evidence/{item_id}", summary="保存评分项关联的资料（用户确认；空列表即取消关联）")
def set_evidence_links(project_id: str, item_id: str, asset_keys: List[str] = Body(..., embed=True)):
    keys = list(dict.fromkeys(asset_keys))
    for key in keys:
        if parse_key(key)[0] not in MATERIAL_KINDS:
            raise HTTPException(status_code=400, detail=f"只能关联资质、人员或业绩资料：{key}")
    found, missing = resolve_links(keys)
    if missing:
        raise HTTPException(status_code=400, detail=f"资料不存在：{'、'.join(missing)}")
    examples = [a.get("name") or a.get("project_name") for _, a in found if a.get("status") == "example"]
    if examples:
        raise HTTPException(status_code=400, detail=f"预设示例资料不能作为证明材料：{'、'.join(examples)}")

    def mutate(project: Project):
        if item_id not in {it.id for it in rp.target_items(project.tender_analysis)}:
            raise HTTPException(status_code=404, detail="评分项不存在（或不属于本次投标分包）")
        if keys:
            project.evidence_links[item_id] = keys
        else:
            project.evidence_links.pop(item_id, None)

    project_store.update(project_id, mutate)
    return {"status": "success", "item_id": item_id, "asset_keys": keys}
