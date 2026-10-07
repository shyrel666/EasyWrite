"""Word 模板与标书导出路由"""
import base64
import binascii
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.task_manager import task_manager
from app.services.checker.export_preflight import build_preflight
from app.services.project_store import project_store
from app.services.exporter.docx_generator import docx_exporter
from app.services.exporter.template_manager import template_manager

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


def _decode_png(data: str) -> Optional[bytes]:
    payload = data.split(",", 1)[1] if data.startswith("data:") else data
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not raw.startswith(PNG_MAGIC) or len(raw) > MAX_DIAGRAM_BYTES:
        return None
    return raw


def _export_bid(project_id: str, template_id: Optional[str], diagrams: Optional[Dict[str, bytes]] = None):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    out_path = docx_exporter.export_project_to_docx(
        project_name=project.name,
        client_name=project.client_name,
        outline=project.outline,
        facts=project.facts,
        style_cfg=template_manager.get_template(template_id or "gov_standard"),
        diagrams=diagrams,
    )
    return FileResponse(path=str(out_path), filename=out_path.name, media_type=DOCX_MEDIA_TYPE)


@router.get("/templates/list", summary="获取可用的标书 Word 格式排版模板")
def list_word_templates():
    return {"templates": template_manager.list_templates()}


@router.post("/templates/create", summary="创建或更新自定义标书 Word 模板")
def create_custom_template(template_data: Dict[str, Any]):
    saved = template_manager.create_or_update_template(template_data)
    return {"status": "success", "template": saved}


@router.delete("/templates/{template_id}", summary="删除自定义模板（内置模板不可删）")
def delete_template(template_id: str):
    if not template_manager.delete_template(template_id):
        raise HTTPException(status_code=404, detail="模板不存在或为内置模板不可删除")
    return {"status": "success", "deleted_id": template_id}


@router.get("/project/{project_id}/export/preflight", summary="导出前检查清单（未撰写、占位、待核实资料、证明材料、未校审、红线核查、偏离表；只提示不阻止导出）")
def export_preflight(project_id: str):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    latest = task_manager.list(1, project_id=project_id, task_type="compliance_check", with_result=True)
    return build_preflight(project, latest[0] if latest else None)


@router.get("/project/{project_id}/export", summary="导出项目为高保真技术标书 Word（含目录与页码；架构图由服务端简化渲染）")
def export_project_bid(project_id: str, template_id: Optional[str] = "gov_standard"):
    return _export_bid(project_id, template_id)


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
    return _export_bid(project_id, req.template_id, diagrams)


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
    return FileResponse(path=str(out_path), filename=out_path.name, media_type=DOCX_MEDIA_TYPE)
