"""合规核查与八维质检路由"""
import logging
from typing import List

from fastapi import APIRouter, HTTPException

from app.core.task_manager import task_manager
from app.models.schemas import EightDimensionQualityReport
from app.services.project_store import project_store
from app.services.checker.compliance_checker import compliance_checker
from app.services.checker.quality_inspector import quality_inspector

logger = logging.getLogger("easywrite.api.compliance")
router = APIRouter(tags=["合规与质检"])


def _get_project(project_id: str):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def _collect_star_items(project) -> List[str]:
    star_items = []
    if project.deviation_matrix:
        star_items = [it.clause_title for it in project.deviation_matrix if it.is_star]
    if not star_items and project.tender_analysis:
        star_items = list(project.tender_analysis.star_disqualification_items)
    return star_items


@router.post("/project/{project_id}/compliance/check", summary="执行废标项合规审查（LLM 多轮证据核查，后台任务）")
def run_compliance_check(project_id: str):
    project = _get_project(project_id)
    star_items = _collect_star_items(project)

    def _run(ctx):
        ctx.report(5, "汇总★号条款与标书正文")
        report = compliance_checker.check_compliance(
            star_items=star_items, outline=project.outline,
            facts=project.facts, progress=ctx,
        )
        return report.model_dump()

    task_id = task_manager.submit("compliance_check", _run, description=f"合规审查：{len(star_items)} 项★条款")
    return {"task_id": task_id, "star_count": len(star_items)}


@router.post("/project/{project_id}/quality/inspect", response_model=EightDimensionQualityReport, summary="执行标书八维全盘质量体检（规则快扫）")
def run_eight_dimension_quality_audit(project_id: str):
    project = _get_project(project_id)
    star_items = _collect_star_items(project)
    report = quality_inspector.inspect_quality(project.outline, project.facts, star_items, project.tender_analysis)
    return report
