"""章节撰写路由：SSE 流式生成 / 同步生成 / 批量撰写 / 保存 / 润色 / 章节检查 / 定向修订 / 智能完善 / 历史版本"""
import asyncio
import json
import logging
from typing import List, Optional, Tuple

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse

from app.core.llm_client import llm_client
from app.core.task_manager import task_manager
from app.models.schemas import (
    GenerateSectionRequest, GenerateSectionResponse, OutlineNode,
    UpdateSectionRequest, PolishSectionRequest, PolishSectionResponse,
    SectionCheckReport, SectionCheckRequest,
)
from app.services.project_store import project_store, find_node
from app.services.generator.evidence_set import select_evidence
from app.services.generator.section_generator import section_generator, strip_title_heading, writing_inputs as _inputs
from app.services.checker.quality_inspector import quality_inspector
from app.services.checker.section_check import check_section, revision_issues
from app.services.proposals import proposal_store, propose
from app.services.refine import runner as refine_runner
from app.services.refine.policy import DEFAULT_MAX_ROUNDS, MAX_ROUNDS_LIMIT, decide
from app.services.section_content import check_section_revision, save_section_content as _save_section_content
from app.services.version_store import version_store

logger = logging.getLogger("easywrite.api.sections")
router = APIRouter(tags=["智能撰写"])


def _get_project(project_id: str):
    project = project_store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def _request_inputs(project, req: GenerateSectionRequest) -> Tuple[OutlineNode, dict, dict]:
    node = find_node(project.outline, req.section_id)
    if not node:
        raise HTTPException(status_code=404, detail="未找到对应章节")
    check_section_revision(node, req.base_revision)
    evidence_kw, prompt_kw = _inputs(
        project, node,
        section_title=req.section_title, section_path=req.section_path, requirements=req.requirements,
        custom_instruction=req.custom_instruction, pinned_refs=req.pinned_refs, excluded_refs=req.excluded_refs,
    )
    return node, evidence_kw, prompt_kw


def _save_with_revision(*args, **kwargs):
    info = {}
    status = _save_section_content(*args, **kwargs, also=lambda _session, saved: info.update(saved))
    return status, info["revision"]


def _save_ai_result(project, node, content, evidence, source, refs=None):
    """比较任务开始时的修订号与状态；冲突时保留 AI 结果供查看，不自动覆盖人工稿。"""
    try:
        return _save_with_revision(
            project.id, node.id, content, "completed", last_refs=refs, version_source=source,
            expected_revision=node.revision, expected_status=node.status,
        )
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        # 冲突稿基于旧正文，通常已不能直接采纳：不取代本节其他仍可采纳的候选稿（如智能完善的结果）
        try:
            proposal = propose(project, node, content, evidence, origin=source, supersede=False)
        except Exception:
            logger.exception("写回冲突后留存 AI 候选稿失败：%s/%s", project.id, node.id)
            raise exc from None
        exc.detail = {**exc.detail, "proposal_id": proposal["id"],
                      "message": exc.detail["message"] + "；AI 结果已保存为候选稿"}
        raise


@router.post("/project/{project_id}/section/generate/stream", summary="章节流式草拟 (SSE：先推送引用元数据再逐 token 输出)")
async def generate_section_content_stream(project_id: str, req: GenerateSectionRequest):
    project = _get_project(project_id)
    node, evidence_kw, prompt_kw = _request_inputs(project, req)

    async def event_generator():
        full_content = []
        refs: List[dict] = []
        try:
            # 检索 + LLM 重排是同步阻塞调用，放到线程中执行，避免卡住事件循环（期间的自动保存等请求）
            evidence = await asyncio.to_thread(select_evidence, project, node, **evidence_kw)
            async for event in section_generator.draft_section_stream(evidence=evidence, **prompt_kw):
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

        complete_text = strip_title_heading("".join(full_content), prompt_kw["section_title"])
        if complete_text.strip():
            try:
                # 按 project_id 重新读取最新项目写回：流式期间其他章节的编辑不会被旧快照覆盖
                status, revision = _save_ai_result(project, node, complete_text, evidence, "ai_generate", refs)
                # content 为最终落库正文（去掉了重复的章节标题行），前端以此为准
                yield f"data: {json.dumps({'done': True, 'section_id': req.section_id, 'status': status, 'revision': revision, 'mode': llm_client.get_mode(), 'content': complete_text}, ensure_ascii=False)}\n\n"
            except HTTPException as e:
                detail = e.detail if isinstance(e.detail, dict) else {"message": str(e.detail)}
                yield f"data: {json.dumps({**detail, 'done': False, 'status_code': e.status_code, 'error': detail['message']}, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': f'内容生成完成但保存失败: {e}'}, ensure_ascii=False)}\n\n"
        else:
            yield f"data: {json.dumps({'done': False, 'error': '生成内容为空'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/project/{project_id}/section/generate", response_model=GenerateSectionResponse, summary="章节同步完整草拟（降级路径）")
def generate_section_content(project_id: str, req: GenerateSectionRequest):
    project = _get_project(project_id)
    node, evidence_kw, prompt_kw = _request_inputs(project, req)
    evidence = select_evidence(project, node, **evidence_kw)
    result = section_generator.draft_section(evidence=evidence, **prompt_kw)
    _, revision = _save_ai_result(project, node, result["generated_content"], evidence,
                                  "ai_generate", result["references"])

    return GenerateSectionResponse(
        section_id=req.section_id,
        generated_content=result["generated_content"],
        reference_sources=result["references"],
        retrieval_message=result.get("retrieval_message", ""),
        mode=result.get("mode", "llm"),
        tokens_used=len(result["generated_content"]),
        revision=revision,
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


@router.post("/project/{project_id}/sections/generate-batch",
             summary="批量撰写章节（后台任务：空白章节直接写入；勾选包括已写章节时，已有正文的章节生成候选稿）")
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
        generated, proposed, skipped, failed = [], [], [], []
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
                evidence_kw, prompt_kw = _inputs(latest, node)
                evidence = select_evidence(latest, node, **evidence_kw)
                result = section_generator.draft_section(evidence=evidence, **prompt_kw)
            except Exception as e:
                logger.exception("批量撰写章节失败 %s", sid)
                failed.append({"id": sid, "title": node.title, "reason": str(e)[:120]})
                continue
            if result.get("mode") != "llm" or not result["generated_content"].strip():
                # 模型调用失败退回了演示样例：不写入
                failed.append({"id": sid, "title": node.title, "reason": "模型调用失败，未写入"})
                continue
            if before.strip():
                # 已有正文：不直接覆盖，存为候选稿（附规则检查报告），由用户查看差异后采纳
                try:
                    proposal = propose(latest, node, result["generated_content"], evidence,
                                       origin="batch", task_id=ctx.task_id)
                except Exception as e:
                    logger.exception("批量撰写生成候选稿失败 %s", sid)
                    failed.append({"id": sid, "title": node.title, "reason": f"候选稿保存失败：{str(e)[:80]}"})
                    continue
                proposed.append({"id": sid, "title": node.title, "proposal_id": proposal["id"]})
                continue
            try:
                status = _save_section_content(
                    project_id, sid, result["generated_content"], "completed",
                    last_refs=result["references"], version_source="batch", expect_content=before,
                )
            except Exception as e:  # 正文与版本整体回滚：本节保持原样，继续写下一节
                logger.exception("批量撰写保存失败 %s", sid)
                failed.append({"id": sid, "title": node.title, "reason": f"保存失败，未写入：{str(e)[:80]}"})
                continue
            if status is None:
                skipped.append(sid)  # 生成期间用户改过该章节：保留用户内容
            else:
                generated.append(sid)
        return {"total": len(target_ids), "generated": generated, "proposed": proposed,
                "skipped": skipped, "failed": failed}

    task_id = task_manager.submit("section_batch", _run, description=f"批量撰写 {len(target_ids)} 个章节")
    return {"task_id": task_id, "total": len(target_ids)}


@router.put("/project/{project_id}/section", summary="人工保存章节正文（自动保存/显式校审）")
def update_section_content(project_id: str, req: UpdateSectionRequest):
    # "已校审"只能由用户显式设置；未传或无效的状态按正文是否为空取 completed / pending
    if req.status in ("pending", "completed", "reviewed"):
        status = req.status
    else:
        status = "completed" if req.content.strip() else "pending"
    _, revision = _save_with_revision(project_id, req.section_id, req.content, status)
    return {"status": "success", "section_id": req.section_id, "node_status": status, "revision": revision}


@router.post("/project/{project_id}/section/polish", response_model=PolishSectionResponse, summary="单章节深度降AI味与公文严肃化润色")
def polish_section(project_id: str, req: PolishSectionRequest):
    project = _get_project(project_id)
    node = find_node(project.outline, req.section_id)
    if not node:
        raise HTTPException(status_code=404, detail="未找到对应章节")
    check_section_revision(node, req.base_revision)
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="章节尚无内容，请先撰写后再润色")
    evidence = select_evidence(project, node, retrieve=False)
    resp = quality_inspector.polish_section(req, project.facts)
    # 润色是 AI 改写，不等于人工校审：状态为 completed，"已校审"只能由用户标记
    _, resp.revision = _save_ai_result(project, node, resp.polished_content, evidence, "polish")
    return resp


# ---------------- 章节检查 ----------------

@router.post("/project/{project_id}/section/{section_id}/check", response_model=SectionCheckReport,
             summary="检查章节正文（规则离线可用；可选加做模型评审）")
def check_section_content(project_id: str, section_id: str, req: Optional[SectionCheckRequest] = Body(default=None)):
    req = req or SectionCheckRequest()
    project = _get_project(project_id)
    node = find_node(project.outline, section_id)
    if not node:
        raise HTTPException(status_code=404, detail="未找到对应章节")
    text = node.content if req.content is None else req.content
    # 只选企业资料与招标原文，不检索知识库（不产生重排调用）；模型评审另参考上次撰写用到的知识库片段
    evidence = select_evidence(project, node, retrieve=False)
    evidence.refs = [r for r in node.last_refs if r.get("ref_type", "kb") == "kb"]
    return check_section(project, node, text, evidence, llm_review=req.llm_review)


@router.post("/project/{project_id}/section/{section_id}/revise",
             summary="基于当前正文定向修订（后台任务，需已配置模型）：结果存为候选稿，经采纳才写入正文")
def revise_section(
    project_id: str, section_id: str,
    parent_id: str = Body(default="", embed=True),
    instruction: str = Body(default="", embed=True),
):
    project = _get_project(project_id)
    node = find_node(project.outline, section_id)
    if not node:
        raise HTTPException(status_code=404, detail="未找到对应章节")
    if not llm_client.is_configured:
        raise HTTPException(status_code=400, detail="定向修订需要先配置大模型（检查本章不需要模型）")
    if parent_id and not proposal_store.get(project_id, parent_id):
        raise HTTPException(status_code=404, detail="候选稿不存在")
    if not node.content.strip():
        raise HTTPException(status_code=400, detail="本节尚无正文，请先撰写")
    precheck = check_section(project, node, node.content, select_evidence(project, node, retrieve=False))
    if not revision_issues(precheck, instruction):
        raise HTTPException(status_code=400, detail="当前正文没有检查出问题；如需改写，请填写修订要求")

    def _run(ctx):
        latest = project_store.get(project_id)
        current = find_node(latest.outline, section_id) if latest else None
        if current is None or not current.content.strip():
            raise RuntimeError("章节已删除或正文为空，未修订")
        ctx.report(10, "选择资料")
        evidence_kw, prompt_kw = _inputs(latest, current)
        evidence = select_evidence(latest, current, **evidence_kw)
        if ctx.cancelled():
            return {"section_id": section_id, "proposal_id": None}
        ctx.report(35, "检查当前正文")
        issues = revision_issues(check_section(latest, current, current.content, evidence), instruction)
        if not issues:
            return {"section_id": section_id, "proposal_id": None, "message": "当前正文没有检查出问题，未修订"}
        ctx.report(50, f"定向修订：{len(issues)} 个问题")
        result = section_generator.revise_section(evidence=evidence, base_text=current.content, issues=issues, **prompt_kw)
        if result["mode"] != "llm" or not result["generated_content"].strip():
            raise RuntimeError("模型调用失败，未生成候选稿")
        ctx.report(90, "检查修订稿")
        proposal = propose(latest, current, result["generated_content"], evidence,
                           origin="revise", task_id=ctx.task_id, parent_id=parent_id)
        return {"section_id": section_id, "proposal_id": proposal["id"], "issues": len(issues),
                "blocking_count": proposal["blocking_count"], "quality_count": proposal["quality_count"]}

    task_id = task_manager.submit("section_revise", _run, description=f"定向修订：{node.title}",
                                  meta={"section_id": section_id})
    return {"task_id": task_id}


# ---------------- 智能完善（写—查—改闭环） ----------------

@router.post("/project/{project_id}/section/{section_id}/refine",
             summary="智能完善（后台任务，需已配置模型）：起草或以当前正文为原稿 → 检查 → 定向修订（默认最多 2 轮），产出带检查报告的候选稿")
def refine_section(
    project_id: str, section_id: str,
    instruction: str = Body(default="", embed=True),
    max_rounds: int = Body(default=DEFAULT_MAX_ROUNDS, embed=True),
    apply_if_blank: bool = Body(default=False, embed=True),
    resume: bool = Body(default=False, embed=True),
):
    project = _get_project(project_id)
    node = find_node(project.outline, section_id)
    if not node:
        raise HTTPException(status_code=404, detail="未找到对应章节")
    if not llm_client.is_configured:
        raise HTTPException(status_code=400, detail="智能完善需要先配置大模型（检查本章不需要模型）")
    if not 0 <= max_rounds <= MAX_ROUNDS_LIMIT:
        raise HTTPException(status_code=400, detail=f"修订轮数只能为 0–{MAX_ROUNDS_LIMIT} 轮")
    if resume:
        if refine_runner.resume_tip(project_id, section_id) is None:
            raise HTTPException(status_code=400, detail="没有可继续的智能完善候选稿（已采纳、放弃或被取代），请重新发起")
    elif node.content.strip() and not instruction.strip():
        precheck = check_section(project, node, node.content, select_evidence(project, node, retrieve=False))
        if decide([precheck], 0, max_rounds).outcome == "goal_met":
            raise HTTPException(status_code=400, detail="当前正文已通过检查；如需改写，请填写补充要求")
    if not refine_runner.claim(project_id, section_id):
        raise HTTPException(status_code=409, detail="本节已有智能完善在进行，请等待其结束")

    def _run(ctx):
        try:
            return refine_runner.run_refine(ctx, project_id, section_id, instruction=instruction,
                                            max_rounds=max_rounds, apply_if_blank=apply_if_blank, resume=resume)
        finally:
            refine_runner.release(project_id, section_id)

    try:
        task_id = task_manager.submit("section_refine", _run, description=f"{'继续' if resume else ''}智能完善：{node.title}",
                                      meta={"section_id": section_id})
    except Exception:
        refine_runner.release(project_id, section_id)
        raise
    return {"task_id": task_id}


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
    status, revision = _save_with_revision(project_id, section_id, version.content, "completed", version_source="restore")
    return {"status": "success", "section_id": section_id, "node_status": status, "content": version.content, "revision": revision}
