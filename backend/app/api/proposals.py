"""章节候选稿路由：列表 / 详情 / 采纳 / 放弃（AI 对已有正文的改动经人确认后才落库）"""
from fastapi import APIRouter, HTTPException, Query

from app.services.project_store import project_store
from app.services.proposals import (
    apply_proposal, proposal_state, proposal_store, reject_proposal, to_dict,
)

router = APIRouter(tags=["候选稿"])


def _project(project_id: str):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


@router.get("/project/{project_id}/proposals", summary="项目的候选稿（open=true 只看待处理的；counts 为各章节待处理数）")
def list_project_proposals(project_id: str, open_only: bool = Query(default=True, alias="open")):
    _project(project_id)
    rows = proposal_store.list(project_id, open_only=open_only)
    counts: dict = {}
    for r in rows:
        if r.status in ("draft", "checked"):
            counts[r.section_id] = counts.get(r.section_id, 0) + 1
    return {"counts": counts, "items": [to_dict(r, with_content=False) for r in rows]}


@router.get("/project/{project_id}/section/{section_id}/proposals", summary="章节的候选稿列表（新→旧，不含正文）")
def list_section_proposals(project_id: str, section_id: str):
    _project(project_id)
    return {"items": [to_dict(r, with_content=False) for r in proposal_store.list(project_id, section_id)]}


@router.get("/project/{project_id}/section/{section_id}/proposals/{proposal_id}",
            summary="候选稿详情：正文、检查报告、所用依据，以及与当前正文/依据的比对")
def get_section_proposal(project_id: str, section_id: str, proposal_id: str):
    project = _project(project_id)
    row = proposal_store.get(project_id, proposal_id)
    if row is None or row.section_id != section_id:
        raise HTTPException(status_code=404, detail="候选稿不存在")
    return {**to_dict(row), "state": proposal_state(project, row)}


@router.post("/project/{project_id}/section/{section_id}/proposals/{proposal_id}/apply",
             summary="采纳候选稿（幂等；正文或依据已变化时返回 409）")
def apply_section_proposal(project_id: str, section_id: str, proposal_id: str):
    _project(project_id)
    return apply_proposal(project_id, section_id, proposal_id)


@router.post("/project/{project_id}/section/{section_id}/proposals/{proposal_id}/reject", summary="放弃候选稿")
def reject_section_proposal(project_id: str, section_id: str, proposal_id: str):
    _project(project_id)
    return reject_proposal(project_id, section_id, proposal_id)
