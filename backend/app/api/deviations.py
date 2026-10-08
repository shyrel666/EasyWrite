"""技术偏离表路由：确定性抽取 / 手编落库 / 批量AI响应（后台任务）/ 回填大纲"""
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Body

from app.core.task_manager import task_manager
from app.models.schemas import DeviationItem
from app.services.assets.evidence import generation_filter
from app.services.project_store import project_store
from app.services.section_content import save_section_content
from app.services.parser.deviation_engine import deviation_engine

logger = logging.getLogger("easywrite.api.deviation")
router = APIRouter(tags=["技术偏离表"])


def _get_project(project_id: str):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


@router.post("/project/{project_id}/deviation/extract", summary="从招标文件正文确定性提取技术指标构建偏离表")
def extract_project_deviations(project_id: str, tender_content: Optional[str] = Body(default=None, embed=True)):
    project = _get_project(project_id)
    tender_text = project_store.get_tender_text(project_id)
    structure = None if tender_content else project_store.get_tender_structure(project_id)

    # 优先按章节树只抽技术/服务需求篇；无章节树（粘贴文本/旧项目）或未定位到需求篇时退回全文扫描
    extracted, scope = [], None
    if structure:
        extracted, scope = deviation_engine.extract_from_structure(structure.get("sections", []), tender_text)
    raw_text = tender_content or tender_text or project.description or ""
    if not extracted:
        if not raw_text.strip():
            raise HTTPException(status_code=400, detail="无招标文件正文可抽取（请先上传招标文件或传入 tender_content）")
        extracted = deviation_engine.extract_requirements(raw_text)
        # 全文扫描模式：拆标识别出的废标红线如未覆盖则补充合并
        if project.tender_analysis and project.tender_analysis.star_disqualification_items:
            existing = {d.clause_title.replace(" ", "")[:60] for d in extracted}
            for star in project.tender_analysis.star_disqualification_items:
                if star.replace(" ", "")[:60] not in existing:
                    extracted.append(DeviationItem(
                        index=len(extracted) + 1, clause_title=star, is_star=True, level="redline",
                        response_status="待生成", response_detail="",
                    ))

    def mutate(latest):
        latest.deviation_matrix = extracted

    project_store.update(project_id, mutate)
    coverage = deviation_engine.coverage_report(extracted, tender_text or raw_text, scope)
    return {"status": "success", "total_items": len(extracted), "items": extracted, "coverage": coverage}


@router.get("/project/{project_id}/deviation", summary="获取项目当前技术偏离表")
def get_project_deviations(project_id: str):
    project = _get_project(project_id)
    return {"items": project.deviation_matrix}


@router.put("/project/{project_id}/deviation", summary="保存人工编辑的偏离表（修复旧版编辑不落库问题）")
def save_project_deviations(project_id: str, items: List[DeviationItem] = Body(...)):
    for i, item in enumerate(items, 1):
        item.index = i

    def mutate(project):
        project.deviation_matrix = items

    project_store.update(project_id, mutate)
    return {"status": "success", "total_items": len(items)}


@router.post("/project/{project_id}/deviation/generate", summary="批量生成点对点技术响应（仅补全“待生成”条目，后台任务）")
def generate_project_deviations(project_id: str):
    project = _get_project(project_id)
    if not project.deviation_matrix:
        raise HTTPException(status_code=400, detail="偏离表为空，请先执行指标提取")

    # 已有响应（人工填写或此前生成）的条目是投标承诺，不得被批量覆盖；需重写的请先改回“待生成”
    pending = [it for it in project.deviation_matrix if it.response_status == "待生成"]
    skipped = len(project.deviation_matrix) - len(pending)
    if not pending:
        raise HTTPException(status_code=400, detail="没有“待生成”的条目；如需重写某条响应，请先将其状态改为“待生成”")

    def _run(ctx):
        answered = deviation_engine.batch_generate_responses(
            pending, project.facts, progress=ctx, exclude=generation_filter(project))
        by_clause = {it.clause_title: it for it in answered if it.response_status != "待生成"}

        def merge(latest):
            # 写回最新偏离表：只回填仍为“待生成”的同一条款，生成期间的人工编辑优先
            for item in latest.deviation_matrix:
                src = by_clause.get(item.clause_title)
                if src and item.response_status == "待生成":
                    item.response_status = src.response_status
                    item.response_detail = src.response_detail
            return latest.deviation_matrix

        items = project_store.update(project_id, merge)
        return {
            "total_items": len(items),
            "generated_count": len(by_clause),
            "skipped_count": skipped,
            "pending_count": sum(1 for it in items if it.response_status == "待生成"),
            "items": [it.model_dump() for it in items],
        }

    task_id = task_manager.submit(
        "deviation_generate", _run,
        description=f"偏离表批量响应：待生成 {len(pending)} 项（跳过已有响应 {skipped} 项）",
    )
    return {"task_id": task_id, "pending_count": len(pending), "skipped_count": skipped}


@router.post("/project/{project_id}/deviation/inject", summary="将偏离表回填至指定大纲章节（覆盖前自动留版）")
def inject_deviations_to_outline(project_id: str, section_id: str = Body(..., embed=True)):
    project = _get_project(project_id)
    if not project.deviation_matrix:
        raise HTTPException(status_code=400, detail="偏离表为空")
    table = deviation_engine.to_markdown_table(project.deviation_matrix)
    save_section_content(project_id, section_id, table, "completed", version_source="deviation",
                         content_mode="point_to_point")
    return {"status": "success", "section_id": section_id}
