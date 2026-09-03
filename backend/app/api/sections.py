"""章节撰写路由：SSE 流式生成 / 同步生成 / 保存 / 润色"""
import json
import logging
from typing import List

from fastapi import APIRouter, HTTPException, Body
from fastapi.responses import StreamingResponse

from app.core.llm_client import llm_client
from app.models.schemas import (
    GenerateSectionRequest, GenerateSectionResponse,
    UpdateSectionRequest, PolishSectionRequest, PolishSectionResponse, OutlineNode,
)
from app.services.project_store import project_store
from app.services.generator.section_generator import section_generator
from app.services.checker.quality_inspector import quality_inspector

logger = logging.getLogger("easywrite.api.sections")
router = APIRouter(tags=["智能撰写"])


def _find_node(nodes: List[OutlineNode], section_id: str):
    for n in nodes:
        if n.id == section_id:
            return n
        found = _find_node(n.children, section_id)
        if found:
            return found
    return None


def _get_project(project_id: str):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def _generator_kwargs(project, req: GenerateSectionRequest) -> dict:
    node = _find_node(project.outline, req.section_id)
    return dict(
        section_title=req.section_title,
        section_path=req.section_path or (node.path if node else req.section_title),
        requirements=req.requirements,
        custom_instruction=req.custom_instruction or "",
        facts=project.facts,
        outline=project.outline,
        section_id=req.section_id,
        project_context=f"{project.name}（客户：{project.client_name}）",
        pinned_refs=req.pinned_refs or (node.pinned_refs if node else []),
        excluded_refs=req.excluded_refs or (node.excluded_refs if node else []),
    )


def _save_section_content(project, section_id: str, content: str, status: str):
    node = _find_node(project.outline, section_id)
    if not node:
        raise HTTPException(status_code=404, detail="未找到对应章节")
    node.content = content
    node.status = status
    if project.stage != "writing":
        project.stage = "writing"
    project_store.save(project)
    return node


@router.post("/project/{project_id}/section/generate/stream", summary="章节流式草拟 (SSE：先推送引用元数据再逐 token 输出)")
async def generate_section_content_stream(project_id: str, req: GenerateSectionRequest):
    project = _get_project(project_id)
    kwargs = _generator_kwargs(project, req)

    async def event_generator():
        full_content = []
        try:
            async for event in section_generator.draft_section_stream(**kwargs):
                if "token" in event:
                    full_content.append(event["token"])
                    yield f"data: {json.dumps({'token': event['token']}, ensure_ascii=False)}\n\n"
                else:
                    payload = {
                        "refs": event.get("refs", []),
                        "retrieval_message": event.get("retrieval_message", ""),
                        "mode": event.get("mode", "llm"),
                    }
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("流式生成失败")
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

        complete_text = "".join(full_content)
        if complete_text.strip():
            try:
                node = _save_section_content(project, req.section_id, complete_text, "completed")
                yield f"data: {json.dumps({'done': True, 'section_id': req.section_id, 'status': node.status, 'mode': llm_client.get_mode()}, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': f'内容生成完成但保存失败: {e}'}, ensure_ascii=False)}\n\n"
        else:
            yield f"data: {json.dumps({'done': False, 'error': '生成内容为空'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/project/{project_id}/section/generate", response_model=GenerateSectionResponse, summary="章节同步完整草拟（批量/降级路径）")
def generate_section_content(project_id: str, req: GenerateSectionRequest):
    project = _get_project(project_id)
    result = section_generator.draft_section(**_generator_kwargs(project, req))
    _save_section_content(project, req.section_id, result["generated_content"], "completed")

    return GenerateSectionResponse(
        section_id=req.section_id,
        generated_content=result["generated_content"],
        reference_sources=result["references"],
        retrieval_message=result.get("retrieval_message", ""),
        mode=result.get("mode", "llm"),
        tokens_used=len(result["generated_content"]),
    )


@router.put("/project/{project_id}/section", summary="人工保存章节正文（自动保存/显式校审）")
def update_section_content(project_id: str, req: UpdateSectionRequest):
    project = _get_project(project_id)
    status = req.status if req.status in ("pending", "completed", "reviewed") else "reviewed"
    _save_section_content(project, req.section_id, req.content, status)
    return {"status": "success", "section_id": req.section_id, "node_status": status}


@router.post("/project/{project_id}/section/polish", response_model=PolishSectionResponse, summary="单章节深度降AI味与公文严肃化润色")
def polish_section(project_id: str, req: PolishSectionRequest):
    project = _get_project(project_id)
    resp = quality_inspector.polish_section(req, project.facts)
    _save_section_content(project, req.section_id, resp.polished_content, "reviewed")
    return resp
