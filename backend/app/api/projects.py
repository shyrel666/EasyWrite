"""项目管理路由"""
from typing import List

from fastapi import APIRouter, HTTPException, Body

from app.models.schemas import (
    Project, ProjectCreate, GlobalFacts,
    OutlineUpdateRequest, ProjectListItem, BlindBidConfig,
)
from app.services.project_store import find_node, merge_outline, project_store
from app.services.proposals import proposal_store
from app.services.version_store import version_store
from app.services.checker.verification import project_verification, verification_counts
from app.services.project_store import now_str

router = APIRouter(tags=["项目管理"])


def _get_or_404(project_id: str) -> Project:
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


@router.get("/projects", response_model=List[ProjectListItem], summary="获取所有标书项目列表与编纂进度")
def list_projects():
    return project_store.list()


@router.post("/project/create", response_model=Project, summary="新建标书项目（向导入口，不含大纲）")
def create_project(req: ProjectCreate):
    return project_store.create(req)


@router.get("/project/{project_id}", response_model=Project, summary="获取项目详情与大纲树")
def get_project(project_id: str):
    return _get_or_404(project_id)


@router.delete("/project/{project_id}", summary="删除指定标书项目")
def delete_project(project_id: str):
    if not project_store.delete(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    version_store.delete_project(project_id)
    proposal_store.delete_project(project_id)
    return {"status": "success", "deleted_id": project_id}


@router.put("/project/{project_id}/facts", summary="更新项目全局事实设定（Global Facts）")
def update_project_facts(project_id: str, facts: GlobalFacts):
    def mutate(project: Project):
        project.facts = facts

    project_store.update(project_id, mutate)
    return {"status": "success", "facts": facts}


@router.put("/project/{project_id}/blind-bid", summary="保存暗标技术册设置（enabled 为 null 表示沿用导出模板默认）")
def update_blind_bid(project_id: str, config: BlindBidConfig):
    # 导出格式设置不是标书内容，不刷新 updated_at，以免红线核查被误判为过期
    project_store.update(project_id, lambda p: setattr(p, "blind_bid", config), touch=False)
    return config


@router.get("/project/{project_id}/verification", summary="全书核实事项与全部定位")
def get_verification(project_id: str):
    rows = project_verification(_get_or_404(project_id))
    return {"items": rows, **verification_counts(rows)}


@router.put("/project/{project_id}/verification/{stable_key}", summary="记录核实结论，依据变化后失效；不替代资料或事实的确认")
def confirm_verification(project_id: str, stable_key: str, basis_hash: str = Body(...), note: str = Body(...)):
    if not note.strip():
        raise HTTPException(400, "请填写核实结论及依据")

    def mutate(project):
        item = next((v for v in project_verification(project) if v.stable_key == stable_key), None)
        if not item or item.basis_hash != basis_hash:
            raise HTTPException(409, "事项或依据已变化，请重新检查")
        project.verification_decisions[stable_key] = {"basis_hash": basis_hash, "note": note.strip(), "at": now_str()}
        return project.verification_decisions[stable_key]

    return project_store.update(project_id, mutate)


@router.put("/project/{project_id}/outline", summary="人工编辑保存大纲树（结构、标题、字数预算；已有章节的正文与状态以服务端为准）")
def update_project_outline(project_id: str, req: OutlineUpdateRequest):
    def mutate(project: Project):
        project.outline, removed = merge_outline(project.outline, req.outline)
        if project.stage in ("created", "tender_analyzed"):
            project.stage = "outline_confirmed"
        return project.stage, removed

    stage, removed = project_store.update(project_id, mutate)
    proposal_store.supersede(project_id, removed)  # 被删除章节的待处理候选稿作废
    return {"status": "success", "stage": stage, "outline": project_store.get(project_id).outline, "removed": removed}


@router.put("/project/{project_id}/stage", summary="更新向导流程阶段")
def update_project_stage(project_id: str, stage: str = Body(..., embed=True)):
    valid = {"created", "tender_analyzed", "outline_confirmed", "writing"}
    if stage not in valid:
        raise HTTPException(status_code=400, detail=f"非法阶段，可选：{sorted(valid)}")
    _get_or_404(project_id)
    project_store.update_stage(project_id, stage)
    return {"status": "success", "stage": stage}


@router.put("/project/{project_id}/section/refs", summary="设置章节引用锁定/排除（人工在环）")
def update_section_refs(
    project_id: str,
    section_id: str = Body(..., embed=True),
    pinned_refs: List[str] = Body(default_factory=list, embed=True),
    excluded_refs: List[str] = Body(default_factory=list, embed=True),
):
    def mutate(project: Project):
        node = find_node(project.outline, section_id)
        if not node:
            raise HTTPException(status_code=404, detail="未找到对应章节")
        node.pinned_refs = pinned_refs
        node.excluded_refs = excluded_refs

    project_store.update(project_id, mutate)
    return {"status": "success", "section_id": section_id}
