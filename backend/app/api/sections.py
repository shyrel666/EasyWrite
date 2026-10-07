"""章节撰写路由：SSE 流式生成 / 同步生成 / 批量撰写 / 保存 / 润色 / 历史版本"""
import json
import logging
from typing import List, Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse

from app.core.llm_client import llm_client
from app.core.task_manager import task_manager
from app.models.schemas import (
    GenerateSectionRequest, GenerateSectionResponse, OutlineNode,
    UpdateSectionRequest, PolishSectionRequest, PolishSectionResponse,
)
from app.services.project_store import project_store, find_node
from app.services.generator.section_generator import section_generator, strip_title_heading
from app.services.checker.quality_inspector import quality_inspector
from app.services.version_store import version_store

logger = logging.getLogger("easywrite.api.sections")
router = APIRouter(tags=["智能撰写"])


def _get_project(project_id: str):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def _node_kwargs(project, node: OutlineNode, **overrides) -> dict:
    kwargs = dict(
        section_title=node.title,
        section_path=node.path or node.title,
        requirements=node.requirements,
        custom_instruction="",
        facts=project.facts,
        outline=project.outline,
        section_id=node.id,
        project_context=f"{project.name}（客户：{project.client_name}）",
        pinned_refs=node.pinned_refs,
        excluded_refs=node.excluded_refs,
    )
    kwargs.update({k: v for k, v in overrides.items() if v})
    return kwargs


def _generator_kwargs(project, req: GenerateSectionRequest) -> dict:
    node = find_node(project.outline, req.section_id)
    if not node:
        raise HTTPException(status_code=404, detail="未找到对应章节")
    return _node_kwargs(
        project, node,
        section_title=req.section_title, section_path=req.section_path, requirements=req.requirements,
        custom_instruction=req.custom_instruction, pinned_refs=req.pinned_refs, excluded_refs=req.excluded_refs,
    )


def _save_section_content(
    project_id: str, section_id: str, content: str, status: str,
    last_refs: Optional[List[dict]] = None,
    version_source: Optional[str] = None,
    expect_content: Optional[str] = None,
) -> Optional[str]:
    """
    原子写回单个章节（重新读取最新项目，不覆盖其他章节的并发编辑），返回写入后的状态。
    version_source：AI 生成/润色/批量/恢复等覆盖操作记录历史版本（人工自动保存不传）。
    expect_content：仅当章节正文仍等于该值时才写入（批量撰写期间用户改过的章节不覆盖），否则返回 None。
    """
    def mutate(project):
        node = find_node(project.outline, section_id)
        if not node:
            raise HTTPException(status_code=404, detail="未找到对应章节")
        if expect_content is not None and node.content != expect_content:
            return None, None
        old = node.content
        node.content = content
        node.status = status
        if last_refs is not None:
            node.last_refs = last_refs
        if project.stage != "writing":
            project.stage = "writing"
        return node.status, old

    saved_status, old_content = project_store.update(project_id, mutate)
    if saved_status is not None and version_source:
        version_store.record(project_id, section_id, old_content or "", content, version_source)
    return saved_status


@router.post("/project/{project_id}/section/generate/stream", summary="章节流式草拟 (SSE：先推送引用元数据再逐 token 输出)")
async def generate_section_content_stream(project_id: str, req: GenerateSectionRequest):
    project = _get_project(project_id)
    kwargs = _generator_kwargs(project, req)

    async def event_generator():
        full_content = []
        refs: List[dict] = []
        try:
            async for event in section_generator.draft_section_stream(**kwargs):
                if "token" in event:
                    full_content.append(event["token"])
                    yield f"data: {json.dumps({'token': event['token']}, ensure_ascii=False)}\n\n"
                else:
                    refs = event.get("refs", [])
                    payload = {
                        "refs": refs,
                        "retrieval_message": event.get("retrieval_message", ""),
                        "mode": event.get("mode", "llm"),
                    }
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("流式生成失败")
            # 生成中断：不写入不完整正文（前端恢复原内容）
            yield f"data: {json.dumps({'done': False, 'error': str(e)}, ensure_ascii=False)}\n\n"
            return

        complete_text = strip_title_heading("".join(full_content), kwargs["section_title"])
        if complete_text.strip():
            try:
                # 按 project_id 重新读取最新项目写回：流式期间其他章节的编辑不会被旧快照覆盖
                status = _save_section_content(project_id, req.section_id, complete_text, "completed",
                                               last_refs=refs, version_source="ai_generate")
                # content 为最终落库正文（去掉了重复的章节标题行），前端以此为准
                yield f"data: {json.dumps({'done': True, 'section_id': req.section_id, 'status': status, 'mode': llm_client.get_mode(), 'content': complete_text}, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': f'内容生成完成但保存失败: {e}'}, ensure_ascii=False)}\n\n"
        else:
            yield f"data: {json.dumps({'done': False, 'error': '生成内容为空'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/project/{project_id}/section/generate", response_model=GenerateSectionResponse, summary="章节同步完整草拟（降级路径）")
def generate_section_content(project_id: str, req: GenerateSectionRequest):
    project = _get_project(project_id)
    result = section_generator.draft_section(**_generator_kwargs(project, req))
    _save_section_content(project_id, req.section_id, result["generated_content"], "completed",
                          last_refs=result["references"], version_source="ai_generate")

    return GenerateSectionResponse(
        section_id=req.section_id,
        generated_content=result["generated_content"],
        reference_sources=result["references"],
        retrieval_message=result.get("retrieval_message", ""),
        mode=result.get("mode", "llm"),
        tokens_used=len(result["generated_content"]),
    )


# ---------------- 全书批量撰写（后台任务） ----------------

def _batch_targets(outline: List[OutlineNode], include_written: bool, only: Optional[List[str]]) -> List[OutlineNode]:
    """可批量撰写的章节：AI 撰写模式的叶节点，跳过已人工校审的；默认只写空白章节"""
    targets = []

    def walk(nodes):
        for n in nodes:
            if n.children:
                walk(n.children)
                continue
            if only is not None and n.id not in only:
                continue
            if n.content_mode != "ai_generate" or n.status == "reviewed":
                continue
            if n.content.strip() and not include_written:
                continue
            targets.append(n)

    walk(outline)
    return targets


@router.post("/project/{project_id}/sections/generate-batch", summary="批量撰写章节（后台任务：默认只写空白章节，跳过已校审）")
def generate_sections_batch(
    project_id: str,
    include_written: bool = Body(default=False, embed=True),
    section_ids: Optional[List[str]] = Body(default=None, embed=True),
):
    project = _get_project(project_id)
    if not llm_client.is_configured:
        raise HTTPException(status_code=400, detail="批量撰写需要先配置大模型（离线模式只能生成演示样例，不适合批量填充）")
    targets = _batch_targets(project.outline, include_written, section_ids)
    if not targets:
        raise HTTPException(status_code=400, detail="没有需要撰写的章节（已校审、非 AI 撰写模式的章节不参与批量撰写）")
    target_ids = [n.id for n in targets]

    def _run(ctx):
        generated, skipped, failed = [], [], []
        for i, sid in enumerate(target_ids):
            if ctx.cancelled():
                break
            latest = project_store.get(project_id)  # 每节重新读取：已写完的兄弟章节进入跨章上下文
            node = find_node(latest.outline, sid) if latest else None
            if not node or node.status == "reviewed" or (node.content.strip() and not include_written):
                skipped.append(sid)
                continue
            ctx.report(int(i / len(target_ids) * 100), f"撰写 {i + 1}/{len(target_ids)}：{node.title}")
            before = node.content
            try:
                result = section_generator.draft_section(**_node_kwargs(latest, node))
            except Exception as e:
                logger.exception("批量撰写章节失败 %s", sid)
                failed.append({"id": sid, "title": node.title, "reason": str(e)[:120]})
                continue
            if result.get("mode") != "llm" or not result["generated_content"].strip():
                # 模型调用失败退回了演示样例：不写入
                failed.append({"id": sid, "title": node.title, "reason": "模型调用失败，未写入"})
                continue
            status = _save_section_content(
                project_id, sid, result["generated_content"], "completed",
                last_refs=result["references"], version_source="batch", expect_content=before,
            )
            if status is None:
                skipped.append(sid)  # 生成期间用户改过该章节：保留用户内容
            else:
                generated.append(sid)
        return {"total": len(target_ids), "generated": generated, "skipped": skipped, "failed": failed}

    task_id = task_manager.submit("section_batch", _run, description=f"批量撰写 {len(target_ids)} 个章节")
    return {"task_id": task_id, "total": len(target_ids)}


@router.put("/project/{project_id}/section", summary="人工保存章节正文（自动保存/显式校审）")
def update_section_content(project_id: str, req: UpdateSectionRequest):
    status = req.status if req.status in ("pending", "completed", "reviewed") else "reviewed"
    _save_section_content(project_id, req.section_id, req.content, status)
    return {"status": "success", "section_id": req.section_id, "node_status": status}


@router.post("/project/{project_id}/section/polish", response_model=PolishSectionResponse, summary="单章节深度降AI味与公文严肃化润色")
def polish_section(project_id: str, req: PolishSectionRequest):
    project = _get_project(project_id)
    if not find_node(project.outline, req.section_id):
        raise HTTPException(status_code=404, detail="未找到对应章节")
    resp = quality_inspector.polish_section(req, project.facts)
    _save_section_content(project_id, req.section_id, resp.polished_content, "reviewed", version_source="polish")
    return resp


# ---------------- 章节历史版本 ----------------

@router.get("/project/{project_id}/section/{section_id}/versions", summary="章节历史版本列表（新→旧）")
def list_section_versions(project_id: str, section_id: str):
    _get_project(project_id)
    return {"versions": version_store.list(project_id, section_id)}


@router.get("/project/{project_id}/section/{section_id}/versions/{version_id}", summary="获取某个历史版本正文")
def get_section_version(project_id: str, section_id: str, version_id: int):
    version = version_store.get(project_id, section_id, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="历史版本不存在")
    return {"id": version.id, "source": version.source, "created_at": version.created_at, "content": version.content}


@router.post("/project/{project_id}/section/{section_id}/versions/{version_id}/restore", summary="恢复历史版本（恢复前的正文自动留版）")
def restore_section_version(project_id: str, section_id: str, version_id: int):
    version = version_store.get(project_id, section_id, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="历史版本不存在")
    status = _save_section_content(project_id, section_id, version.content, "completed", version_source="restore")
    return {"status": "success", "section_id": section_id, "node_status": status, "content": version.content}
