"""招标文件解析（18项拆标）路由：后台任务 + 进度轮询"""
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Body
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.task_manager import task_manager
from app.models.schemas import TenderAnalysis18
from app.services.parser.word_parser import WordDocumentParser
from app.services.parser.tender_analyzer import tender_analyzer
from app.services.project_store import project_store

logger = logging.getLogger("easywrite.api.tender")
router = APIRouter(tags=["招标文件解析"])
_parser = WordDocumentParser()


@router.post("/tender/analyze", summary="上传招标文件并后台执行18项结构化拆解（返回任务ID）")
async def analyze_tender_document(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="目前仅支持上传 .docx 格式招标文件")

    content = await file.read()
    saved_path = settings.UPLOAD_DIR / f"tender_{uuid.uuid4().hex[:8]}_{Path(file.filename).name}"
    saved_path.write_bytes(content)

    def _run(ctx):
        ctx.report(10, "解析 Word 文档结构")
        parsed = _parser.parse_docx(saved_path)
        all_text = "\n".join(sec.get("content", "") for sec in parsed.get("sections", []))
        tables_text = []
        for sec in parsed.get("sections", []):
            for tbl in sec.get("tables", []):
                tables_text.append("\n".join(" | ".join(r) for r in tbl))
        full_text = all_text + ("\n\n" + "\n\n".join(tables_text) if tables_text else "")
        if not full_text.strip():
            raise ValueError("招标文件解析后无有效文本（可能为扫描件或空文档）")
        ctx.report(30, f"正文 {len(full_text)} 字，开始 LLM 结构化抽取")
        analysis = tender_analyzer.analyze_text(full_text, filename_hint=file.filename)
        if not analysis.project_name:
            analysis.project_name = Path(file.filename).stem
        ctx.report(100, "拆解完成")
        return analysis.model_dump()

    task_id = task_manager.submit("tender_analyze", _run, description=f"拆标分析：{file.filename}")
    return {"task_id": task_id, "filename": file.filename}


@router.post("/tender/analyze/text", summary="直接粘贴文本执行18项拆解（轻量入口）")
def analyze_tender_text(text: str = Body(..., embed=True)):
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="招标文件文本为空")
    analysis = tender_analyzer.analyze_text(text)
    return analysis


@router.post("/project/{project_id}/tender/apply", summary="将18项拆标结果应用到项目（联动事实/偏离表建议）")
def apply_tender_analysis(project_id: str, analysis: TenderAnalysis18, tender_text: str = Body(default="", embed=True)):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    project.tender_analysis = analysis
    if analysis.purchaser_name and not project.client_name:
        project.client_name = analysis.purchaser_name

    # 联动填充全局事实（只补充空字段，不覆盖用户已填内容）
    if analysis.duration_requirement and analysis.duration_requirement != "未提及" and not project.facts.delivery_guarantee:
        project.facts.delivery_guarantee = f"承诺在【{analysis.duration_requirement}】内保质完成整体交付"
    if analysis.warranty_period and analysis.warranty_period != "未提及" and not project.facts.sla_commitment:
        project.facts.sla_commitment = f"承诺提供【{analysis.warranty_period}】及7×24小时全天候响应保障"

    if project.stage == "created":
        project.stage = "tender_analyzed"
    project_store.save(project)

    if tender_text and tender_text.strip():
        project_store.set_tender_text(project_id, tender_text)

    return {
        "status": "success",
        "stage": project.stage,
        "facts": project.facts,
        "star_count": len(analysis.star_disqualification_items),
    }


@router.get("/project/{project_id}/tender/text", summary="获取项目已存档的招标文件正文")
def get_tender_text(project_id: str):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    text = project_store.get_tender_text(project_id)
    return {"has_text": bool(text), "length": len(text), "text": text[:5000]}
