"""招标文件解析（18项拆标）路由：后台任务 + 进度轮询"""
import logging
import uuid
from pathlib import Path

from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Body

from app.core.config import settings
from app.core.task_manager import task_manager
from app.models.schemas import TenderAnalysis18
from app.services.parser.commitments import suggest_commitments
from app.services.parser.document_parser import describe_source, parse_document, unsupported_reason
from app.services.parser.tender_analyzer import tender_analyzer
from app.services.project_store import project_store

logger = logging.getLogger("easywrite.api.tender")
router = APIRouter(tags=["招标文件解析"])


@router.post("/tender/analyze", summary="上传招标文件（.docx / .pdf）并后台执行18项结构化拆解 + 评分细则抽取（返回任务ID）")
async def analyze_tender_document(file: UploadFile = File(...), project_id: Optional[str] = Form(default=None)):
    reason = unsupported_reason(file.filename)
    if reason:
        raise HTTPException(status_code=400, detail=reason)
    if project_id and not project_store.get(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")

    content = await file.read()
    saved_path = settings.UPLOAD_DIR / f"tender_{uuid.uuid4().hex[:8]}_{Path(file.filename).name}"
    saved_path.write_bytes(content)

    def _run(ctx):
        ctx.report(10, "解析 PDF 页面与表格" if saved_path.suffix.lower() == ".pdf" else "解析 Word 文档结构")
        parsed = parse_document(saved_path)  # PDF 扫描件/加密/乱码时抛出可读的 PdfParseError
        full_text = parsed.get("full_text", "")
        if not full_text.strip():
            raise ValueError("招标文件解析后无有效文本（可能为扫描件或空文档）")
        if project_id:
            # 招标原文是后续偏离表抽取 / 合规核查的依据，上传即存档（与是否应用拆标结论无关）
            project_store.set_tender_text(project_id, full_text)
            project_store.set_tender_structure(project_id, parsed)
        ctx.report(30, f"正文 {len(full_text)} 字、表格 {len(parsed.get('tables', []))} 张，开始结构化抽取")
        analysis = tender_analyzer.analyze_document(parsed, filename_hint=file.filename)
        analysis.source_note = describe_source(parsed)
        if not analysis.project_name:
            analysis.project_name = Path(file.filename).stem
        ctx.report(100, "拆解完成")
        return analysis.model_dump()

    task_id = task_manager.submit(
        "tender_analyze", _run, description=f"拆标分析：{file.filename}", project_id=project_id or "",
    )
    return {"task_id": task_id, "filename": file.filename}


@router.post("/tender/analyze/text", summary="直接粘贴文本执行18项拆解（轻量入口）")
def analyze_tender_text(text: str = Body(..., embed=True)):
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="招标文件文本为空")
    analysis = tender_analyzer.analyze_text(text)
    analysis.scoring_note = "粘贴的纯文本无法保留评分表结构，未抽取评分细则；建议上传 .docx 或 .pdf 招标文件"
    return analysis


@router.post("/project/{project_id}/tender/apply", summary="将18项拆标结果应用到项目（返回承诺建议，不改动全局事实）")
def apply_tender_analysis(project_id: str, analysis: TenderAnalysis18, tender_text: str = Body(default="", embed=True)):
    def mutate(project):
        project.tender_analysis = analysis
        if analysis.purchaser_name and not project.client_name:
            project.client_name = analysis.purchaser_name
        # 全局事实是企业承诺，不在这里自动填写：工期/质保等只作为 commitment_suggestions 返回，由用户采纳
        if project.stage == "created":
            project.stage = "tender_analyzed"
        return project

    project = project_store.update(project_id, mutate)

    if tender_text and tender_text.strip():
        project_store.set_tender_text(project_id, tender_text)

    return {
        "status": "success",
        "stage": project.stage,
        "facts": project.facts,
        "star_count": len(analysis.star_disqualification_items),
        "commitment_suggestions": suggest_commitments(analysis),
    }


@router.get("/project/{project_id}/tender/commitment-suggestions", summary="按已应用的拆标结果给出承诺建议（只复述招标要求，采纳后经 PUT facts 写入）")
def get_commitment_suggestions(project_id: str):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"suggestions": suggest_commitments(project.tender_analysis)}


@router.get("/project/{project_id}/tender/text", summary="获取项目已存档的招标文件正文")
def get_tender_text(project_id: str):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    text = project_store.get_tender_text(project_id)
    return {"has_text": bool(text), "length": len(text), "text": text[:5000]}
