"""项目管理路由"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Body

from app.models.schemas import (
    Project, ProjectCreate, GlobalFacts, OutlineNode,
    OutlineUpdateRequest, ProjectListItem,
)
from app.services.project_store import project_store

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
    return {"status": "success", "deleted_id": project_id}


@router.put("/project/{project_id}/facts", summary="更新项目全局事实设定（Global Facts）")
def update_project_facts(project_id: str, facts: GlobalFacts):
    project = _get_or_404(project_id)
    project.facts = facts
    project_store.save(project)
    return {"status": "success", "facts": facts}


@router.put("/project/{project_id}/outline", summary="人工编辑保存大纲树（增删改节点/字数预算）")
def update_project_outline(project_id: str, req: OutlineUpdateRequest):
    project = _get_or_404(project_id)
    project.outline = req.outline
    if project.stage in ("created", "tender_analyzed"):
        project.stage = "outline_confirmed"
    project_store.save(project)
    return {"status": "success", "stage": project.stage}


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
    project = _get_or_404(project_id)

    def walk(nodes: List[OutlineNode]) -> bool:
        for n in nodes:
            if n.id == section_id:
                n.pinned_refs = pinned_refs
                n.excluded_refs = excluded_refs
                return True
            if walk(n.children):
                return True
        return False

    if not walk(project.outline):
        raise HTTPException(status_code=404, detail="未找到对应章节")
    project_store.save(project)
    return {"status": "success", "section_id": section_id}
