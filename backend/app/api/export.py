"""Word 模板与标书导出路由"""
import base64
import binascii
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.background import BackgroundTask

from app.core.task_manager import task_manager
from app.services.checker.export_preflight import build_preflight
from app.services.project_store import project_store
from app.services.exporter.docx_generator import docx_exporter
from app.services.exporter.template_manager import template_manager
from app.services.exporter.scoring_index import scoring_index_rows
from app.models.schemas import EvidenceAppendixEntry, BlindBidConfig
from app.services.exporter.evidence_appendix import AppendixError, appendix_options, prepare_appendices
from app.services.exporter.diagram_spec import diagram_checks
from app.services.exporter.blind_bid import effective_config, inspect_blind, prepare_blind, scrub_docx

router = APIRouter(tags=["模板与导出"])

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_DIAGRAMS = 200
MAX_DIAGRAM_BYTES = 8 * 1024 * 1024


class DiagramImage(BaseModel):
    code: str = Field(..., description="Mermaid 源码（与正文代码块一致，后端按归一化代码匹配）")
    image: str = Field(..., description="前端用 Mermaid 渲染的 PNG（data URL 或 base64）")


class ExportRequest(BaseModel):
    template_id: str = "gov_standard"
    diagrams: List[DiagramImage] = Field(default_factory=list)
    include_scoring_index: bool = True
    appendix_plan: List[EvidenceAppendixEntry] = Field(default_factory=list, max_length=100)
    book_kind: Literal["technical", "qualification"] = "technical"


def _decode_png(data: str) -> Optional[bytes]:
    payload = data.split(",", 1)[1] if data.startswith("data:") else data
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not raw.startswith(PNG_MAGIC) or len(raw) > MAX_DIAGRAM_BYTES:
        return None
    return raw


def _export_bid(project_id: str, template_id: Optional[str], diagrams: Optional[Dict[str, bytes]] = None,
                include_scoring_index: bool = True, appendix_plan=None, book_kind="technical"):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    config = effective_config(project, template_manager.templates.get(template_id or "gov_standard"))
    blind = config.enabled and book_kind == "technical"
    identifiers = []
    if blind:
        if appendix_plan:
            raise HTTPException(400, "暗标技术册不能编入明标证明附件，请单独导出资格证明册")
        project, identifiers = prepare_blind(project, config)
        diagrams = {}
    if book_kind == "qualification" and not appendix_plan:
        raise HTTPException(400, "资格证明册须选择证明附件")
    try:
        appendices = prepare_appendices(project, appendix_plan or []) if appendix_plan else []
    except AppendixError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if book_kind == "qualification":
        for entry in appendices:
            entry.entry.placement = "appendix"
    index = scoring_index_rows(project) if include_scoring_index and book_kind == "technical" else None
    if blind and index:
        for row in index:
            row["attachments"] = []
    out_path = docx_exporter.export_project_to_docx(
        project_name=project.name,
        client_name=project.client_name,
        outline=project.outline if book_kind == "technical" else [],
        facts=project.facts,
        style_cfg=template_manager.get_template(template_id or "gov_standard"),
        diagrams=diagrams,
        scoring_index=index,
        evidence_appendices=appendices,
        include_cover=not blind,
    )
    try:
        if blind:
            scrub_docx(out_path, config, identifiers)
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    suffix = "资格证明册" if book_kind == "qualification" else ("暗标技术册" if blind else "技术标书")
    return FileResponse(path=str(out_path), filename=docx_exporter.download_name(project.name, suffix), media_type=DOCX_MEDIA_TYPE,
                        background=BackgroundTask(out_path.unlink, missing_ok=True))


@router.get("/templates/list", summary="获取可用的标书 Word 格式排版模板")
def list_word_templates():
    return {"templates": template_manager.list_templates()}


@router.post("/templates/create", summary="创建或更新自定义标书 Word 模板")
def create_custom_template(template_data: Dict[str, Any]):
    if "blind_bid" in template_data:
        try:
            template_data["blind_bid"] = BlindBidConfig.model_validate(template_data["blind_bid"] or {}).model_dump()
        except ValidationError as e:
            raise HTTPException(status_code=422, detail="暗标规则无效：" + "；".join(err["msg"] for err in e.errors())) from e
    saved = template_manager.create_or_update_template(template_data)
    return {"status": "success", "template": saved}


@router.delete("/templates/{template_id}", summary="删除自定义模板（内置模板不可删）")
def delete_template(template_id: str):
    if not template_manager.delete_template(template_id):
        raise HTTPException(status_code=404, detail="模板不存在或为内置模板不可删除")
    return {"status": "success", "deleted_id": template_id}


@router.get("/project/{project_id}/export/preflight", summary="导出前检查清单（未撰写、占位、待核实资料、证明材料、未校审、红线核查、偏离表；只提示不阻止导出）")
def export_preflight(project_id: str, template_id: str = "gov_standard"):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    latest = task_manager.list(1, project_id=project_id, task_type="compliance_check", with_result=True)
    return {**build_preflight(project, latest[0] if latest else None), "scoring_index": scoring_index_rows(project),
            "appendix_options": appendix_options(project), "diagram_checks": diagram_checks(project.outline),
            "blind_bid": inspect_blind(project, effective_config(project, template_manager.templates.get(template_id)))}


@router.get("/project/{project_id}/export", summary="导出项目为高保真技术标书 Word（含目录与页码；架构图由服务端简化渲染）")
def export_project_bid(project_id: str, template_id: Optional[str] = "gov_standard", include_scoring_index: bool = True):
    return _export_bid(project_id, template_id, include_scoring_index=include_scoring_index)


@router.post("/project/{project_id}/export", summary="导出技术标书 Word，附带前端用 Mermaid 渲染好的架构图图片")
def export_project_bid_with_diagrams(project_id: str, req: ExportRequest):
    if len(req.diagrams) > MAX_DIAGRAMS:
        raise HTTPException(status_code=400, detail=f"架构图数量超过上限 {MAX_DIAGRAMS}")
    diagrams: Dict[str, bytes] = {}
    for item in req.diagrams:
        raw = _decode_png(item.image)
        if raw is None:
            raise HTTPException(status_code=400, detail="架构图图片不是有效的 PNG（或超过 8MB）")
        diagrams[item.code] = raw
    return _export_bid(project_id, req.template_id, diagrams, req.include_scoring_index, req.appendix_plan, req.book_kind)


@router.get("/project/{project_id}/deviation/export", summary="单独导出技术偏离表 Word（原生表格）")
def export_deviation_table(project_id: str, template_id: Optional[str] = "gov_standard"):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not project.deviation_matrix:
        raise HTTPException(status_code=400, detail="偏离表为空，请先提取招标技术条款")
    out_path = docx_exporter.export_deviation_table(
        project_name=project.name,
        items=project.deviation_matrix,
        style_cfg=template_manager.get_template(template_id or "gov_standard"),
    )
    return FileResponse(path=str(out_path), filename=docx_exporter.download_name(project.name, "技术偏离表"),
                        media_type=DOCX_MEDIA_TYPE, background=BackgroundTask(out_path.unlink, missing_ok=True))
