"""技术偏离表路由：确定性抽取 / 手编落库 / 批量AI响应（后台任务）/ 回填大纲"""
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Body

from app.core.task_manager import task_manager
from app.models.schemas import DeviationItem, OutlineNode
from app.services.project_store import project_store
from app.services.parser.deviation_engine import deviation_engine

logger = logging.getLogger("easywrite.api.deviation")
router = APIRouter(tags=["技术偏离表"])


def _get_project(project_id: str):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def _find_node(nodes: List[OutlineNode], section_id: str):
    for n in nodes:
        if n.id == section_id:
            return n
        found = _find_node(n.children, section_id)
        if found:
            return found
    return None


@router.post("/project/{project_id}/deviation/extract", summary="从招标文件正文确定性提取技术指标构建偏离表")
def extract_project_deviations(project_id: str, tender_content: Optional[str] = Body(default=None, embed=True)):
    project = _get_project(project_id)
    raw_text = tender_content or project_store.get_tender_text(project_id) or project.description or ""
    if not raw_text.strip():
        raise HTTPException(status_code=400, detail="无招标文件正文可抽取（请先上传招标文件或传入 tender_content）")

    extracted = deviation_engine.extract_requirements(raw_text)

    # 18项拆标中的★条款如未覆盖则补充合并
    if project.tender_analysis and project.tender_analysis.star_disqualification_items:
        existing = {d.clause_title.replace(" ", "")[:60] for d in extracted}
        idx = len(extracted) + 1
        for star in project.tender_analysis.star_disqualification_items:
            if star.replace(" ", "")[:60] not in existing:
                extracted.append(DeviationItem(
                    index=idx, clause_title=star, is_star=True,
                    response_status="待生成", response_detail="",
                ))
                idx += 1

    project.deviation_matrix = extracted
    project_store.save(project)
    coverage = deviation_engine.coverage_report(extracted, raw_text)
    return {"status": "success", "total_items": len(extracted), "items": extracted, "coverage": coverage}


@router.get("/project/{project_id}/deviation", summary="获取项目当前技术偏离表")
def get_project_deviations(project_id: str):
    project = _get_project(project_id)
    return {"items": project.deviation_matrix}


@router.put("/project/{project_id}/deviation", summary="保存人工编辑的偏离表（修复旧版编辑不落库问题）")
def save_project_deviations(project_id: str, items: List[DeviationItem] = Body(...)):
    project = _get_project(project_id)
    for i, item in enumerate(items, 1):
        item.index = i
    project.deviation_matrix = items
    project_store.save(project)
    return {"status": "success", "total_items": len(items)}


@router.post("/project/{project_id}/deviation/generate", summary="批量生成点对点技术响应（后台任务）")
def generate_project_deviations(project_id: str):
    project = _get_project(project_id)
    if not project.deviation_matrix:
        raise HTTPException(status_code=400, detail="偏离表为空，请先执行指标提取")

    def _run(ctx):
        answered = deviation_engine.batch_generate_responses(project.deviation_matrix, project.facts, progress=ctx)
        # 写回数据库（task 内重新读取最新项目避免覆盖用户并行编辑）
        latest = project_store.get(project_id)
        if latest:
            latest.deviation_matrix = answered
            project_store.save(latest)
        return {
            "total_items": len(answered),
            "pending_count": sum(1 for it in answered if it.response_status == "待生成"),
            "items": [it.model_dump() for it in answered],
        }

    task_id = task_manager.submit("deviation_generate", _run, description=f"偏离表批量响应：{len(project.deviation_matrix)} 项")
    return {"task_id": task_id}


@router.post("/project/{project_id}/deviation/inject", summary="将偏离表回填至指定大纲章节")
def inject_deviations_to_outline(project_id: str, section_id: str = Body(..., embed=True)):
    project = _get_project(project_id)
    if not project.deviation_matrix:
        raise HTTPException(status_code=400, detail="偏离表为空")
    node = _find_node(project.outline, section_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"未找到目标章节 {section_id}")

    node.content = deviation_engine.to_markdown_table(project.deviation_matrix)
    node.status = "completed"
    node.content_mode = "point_to_point"
    project_store.save(project)
    return {"status": "success", "section_id": section_id}
