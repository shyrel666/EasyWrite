"""招标文件解析（18项拆标）路由：后台任务 + 进度轮询"""
import logging
import hashlib
import uuid
from pathlib import Path

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Body
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.task_manager import task_manager
from app.models.schemas import TenderAnalysis18
from app.services.parser.commitments import structured_requirements, suggest_commitments
from app.services.parser.document_parser import describe_source, parse_document, unsupported_reason
from app.services.parser import tender_reader
from app.services.parser.tender_analyzer import tender_analyzer
from app.services.project_store import find_node, project_store
from app.services import tender_versions as tv

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
        parsed.update(file_sha256=hashlib.sha256(content).hexdigest(), filename=file.filename)
        full_text = parsed.get("full_text", "")
        if not full_text.strip():
            raise ValueError("招标文件解析后无有效文本（可能为扫描件或空文档）")
        if project_id:
            # 招标原文是后续偏离表抽取 / 合规核查的依据，上传即存档（与是否应用拆标结论无关）
            project_store.set_tender_document(project_id, full_text, parsed)
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
    analysis.commitment_requirements = structured_requirements(analysis)
    def mutate(project):
        if any(x.state == "confirmed" for x in project.tender_versions.overrides):
            raise HTTPException(409, "已有生效补遗，请从补遗入口追加，不能整体覆盖拆标结果")
        project.tender_analysis = analysis
        project.tender_versions.base_analysis = analysis.model_copy(deep=True)
        project.tender_versions.base_deviations = [d.model_copy(deep=True) for d in project.deviation_matrix]
        if analysis.purchaser_name and not project.client_name:
            project.client_name = analysis.purchaser_name
        # 全局事实是企业承诺，不在这里自动填写：工期/质保等只作为 commitment_suggestions 返回，由用户采纳
        if project.stage == "created":
            project.stage = "tender_analyzed"
        return project

    def also(session, _project):
        if tender_text and tender_text.strip():
            project_store.set_tender_document_in(session, project_id, tender_text)

    project = project_store.update(project_id, mutate, also=also)

    return {
        "status": "success",
        "stage": project.stage,
        "facts": project.facts,
        "star_count": len(analysis.star_disqualification_items),
        "commitment_suggestions": suggest_commitments(analysis),
    }


class TenderDocumentInput(BaseModel):
    kind: Literal["addendum", "clarification"] = "addendum"
    name: str = Field(min_length=1, max_length=200)
    package: str = ""
    published_at: str = Field(min_length=10, max_length=40)
    text: str = Field(min_length=1, max_length=200_000)


class OverrideInput(BaseModel):
    document_id: str
    clause_id: str
    package: str = ""
    action: Literal["replace", "append", "revoke"] = "replace"
    text: str = Field(default="", max_length=20000)
    source_page: int = Field(default=1, ge=1)
    source_excerpt: str = Field(min_length=1, max_length=20000)
    basis_hash: str
    points: Optional[float] = Field(default=None, ge=0)


@router.get("/project/{project_id}/tender/versions")
def tender_versions(project_id: str):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    rows = list(tv.effective_clauses(project).values())
    return {"documents": project.tender_versions.documents, "overrides": project.tender_versions.overrides,
            "clauses": rows, "suggestions": tv.mapping_suggestions(project),
            "package": project.tender_analysis.target_package if project.tender_analysis else ""}


@router.post("/project/{project_id}/tender/documents")
def add_tender_document(project_id: str, req: TenderDocumentInput):
    return project_store.update(project_id, lambda p: tv.register_document(p, **req.model_dump()))


@router.post("/project/{project_id}/tender/documents/upload")
async def upload_addendum(project_id: str, file: UploadFile = File(...),
                          kind: Literal["addendum", "clarification"] = Form("addendum"),
                          package: str = Form(""), published_at: str = Form(...)):
    if not project_store.get(project_id):
        raise HTTPException(404, "项目不存在")
    reason = unsupported_reason(file.filename)
    if reason:
        raise HTTPException(400, reason)
    content = await file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, "补遗文件不能超过 20MB")
    path = settings.UPLOAD_DIR / (uuid.uuid4().hex + Path(file.filename).suffix)
    try:
        path.write_bytes(content)
        parsed = parse_document(path)
        return project_store.update(project_id, lambda p: tv.register_document(
            p, parsed.get("full_text", ""), kind, file.filename, package, published_at, hashlib.sha256(content).hexdigest()))
    finally:
        path.unlink(missing_ok=True)


@router.post("/project/{project_id}/tender/overrides")
def suggest_override(project_id: str, req: OverrideInput):
    return project_store.update(project_id, lambda p: tv.suggest(p, req))


@router.put("/project/{project_id}/tender/overrides/{override_id}")
def decide_override(project_id: str, override_id: str, state: Literal["confirmed", "withdrawn"] = Body(...),
                    basis_hash: str = Body("")):
    return project_store.update(project_id, lambda p: tv.decide(p, override_id, state, basis_hash))


@router.get("/project/{project_id}/tender/commitment-suggestions", summary="按已应用的拆标结果给出承诺建议（只复述招标要求，采纳后经 PUT facts 写入）")
def get_commitment_suggestions(project_id: str):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"suggestions": suggest_commitments(project.tender_analysis),
            "requirements": structured_requirements(project.tender_analysis) if project.tender_analysis else []}


def _page(text: str, offset: int, limit: int) -> dict:
    """分段返回长文本：start/end 为本段在全文中的位置，truncated 表示全文未读完"""
    total = len(text)
    start = min(max(offset, 0), total)
    end = min(start + max(limit, 1), total)
    return {"text": text[start:end], "start": start, "end": end, "total": total, "truncated": start > 0 or end < total}


@router.get("/project/{project_id}/tender/text", summary="获取项目已存档的招标文件正文（分段读取，truncated 标明是否截断）")
def get_tender_text(project_id: str, offset: int = 0, limit: int = 5000):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    text = project_store.get_tender_text(project_id)
    return {"has_text": bool(text), "length": len(text), **_page(text, offset, limit)}


@router.get("/project/{project_id}/tender/outline", summary="招标文件章节树（ID、标题、层级、路径、字数）；传 section_id 时附带该撰写章节的要点与相关原文章节")
def get_tender_outline(project_id: str, section_id: Optional[str] = None):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    document = project_store.get_tender_document(project_id)
    structure = document["structure"]
    # source：text 粘贴文本（无章节树）/ docx / pdf；旧项目为空
    result = {"has_structure": bool(structure and structure.get("sections")), "source": document["source"],
              "sections": tender_reader.outline_tree(structure), "keywords": [], "related": []}
    node = find_node(project.outline, section_id) if section_id else None
    if node:
        result["keywords"] = tender_reader.node_keywords(node, project.tender_analysis)
        result["related"] = tender_reader.related_sections(
            structure, result["keywords"], primary=tender_reader.own_keywords(node))
    return result


@router.get("/project/{project_id}/tender/section", summary="读取招标文件某一章节的正文（path 为章节 ID 或完整路径；默认含下级章节，分段读取）")
def get_tender_section(project_id: str, path: str, offset: int = 0, limit: int = 20000, deep: bool = True):
    if not project_store.get(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    section = tender_reader.find_section(project_store.get_tender_structure(project_id), path)
    if section is None:
        raise HTTPException(status_code=404, detail="招标文件中没有该章节（或尚未上传招标文件）")
    return {
        "id": section.get("section_id", ""),
        "title": section.get("title", ""),
        "path": section.get("breadcrumb", "") or section.get("title", ""),
        **_page(tender_reader.section_text(section, deep), offset, limit),
    }
