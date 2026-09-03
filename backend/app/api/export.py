"""Word 模板与标书导出路由"""
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.project_store import project_store
from app.services.exporter.docx_generator import docx_exporter
from app.services.exporter.template_manager import template_manager

router = APIRouter(tags=["模板与导出"])


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


@router.get("/project/{project_id}/export", summary="导出项目为高保真技术标书 Word（含目录与页码）")
def export_project_bid(project_id: str, template_id: Optional[str] = "gov_standard"):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    style_cfg = template_manager.get_template(template_id or "gov_standard")
    out_path = docx_exporter.export_project_to_docx(
        project_name=project.name,
        client_name=project.client_name,
        outline=project.outline,
        facts=project.facts,
        style_cfg=style_cfg,
    )

    return FileResponse(
        path=str(out_path),
        filename=out_path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
