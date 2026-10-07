"""大纲规划路由：一级草案（确认门）→ 全树展开"""
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Body

from app.services.project_store import project_store
from app.services.generator.outline_generator import outline_generator

logger = logging.getLogger("easywrite.api.outline")
router = APIRouter(tags=["大纲规划"])


@router.post("/project/{project_id}/outline/draft-level1", summary="生成一级章节草案（人工确认门之前）")
def draft_level1(
    project_id: str,
    description_override: Optional[str] = Body(default=None, embed=True),
):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    result = outline_generator.draft_level1(
        description=description_override or project.description or project.name,
        tender_analysis=project.tender_analysis,
        facts=project.facts,
    )
    return result


@router.post("/project/{project_id}/outline/expand", summary="以确认后的一级章节为骨架展开完整大纲树")
def expand_outline(
    project_id: str,
    chapters: List[dict] = Body(..., embed=True),
    total_word_budget: int = Body(default=30000, embed=True),
):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not chapters:
        raise HTTPException(status_code=400, detail="一级章节列表为空")

    result = outline_generator.expand_full_tree(
        confirmed_chapters=chapters,
        description=project.description or project.name,
        tender_analysis=project.tender_analysis,
        facts=project.facts,
        total_word_budget=total_word_budget,
    )

    def mutate(latest) -> str:
        latest.outline = result["outline"]
        if latest.stage in ("created", "tender_analyzed"):
            latest.stage = "outline_confirmed"
        return latest.stage

    stage = project_store.update(project_id, mutate)
    return {"status": "success", "outline": result["outline"], "mode": result["mode"], "message": result["message"], "stage": stage}
