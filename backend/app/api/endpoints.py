import os
import uuid
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks, Body
from fastapi.responses import FileResponse, StreamingResponse

from app.core.config import settings
from app.core.ai_settings_manager import ai_settings_manager
from app.core.llm_client import llm_client
from app.models.schemas import (
    OutlineNode, Project, ProjectCreate, GlobalFacts,
    KnowledgeChunk, KnowledgeQueryRequest,
    GenerateSectionRequest, GenerateSectionResponse,
    UpdateSectionRequest, TenderAnalysis18, ComplianceCheckReport,
    DeviationItem, ProjectListItem, CompanyQualification,
    PersonnelAsset, CaseContract, SolutionComponent,
    EightDimensionQualityReport, PolishSectionRequest, PolishSectionResponse
)
from app.services.parser.word_parser import WordDocumentParser
from app.services.parser.tender_analyzer import tender_analyzer
from app.services.parser.deviation_engine import deviation_engine
from app.services.rag.vector_store import knowledge_store
from app.services.generator.bid_generator import bid_generator
from app.services.checker.compliance_checker import compliance_checker
from app.services.checker.quality_inspector import quality_inspector
from app.services.assets.asset_manager import asset_manager
from app.services.exporter.docx_generator import docx_exporter
from app.services.exporter.template_manager import template_manager

router = APIRouter()
parser = WordDocumentParser()

# 持久化项目存储
PROJECTS_FILE = settings.DATA_DIR / "projects.json"
PROJECTS_DB: Dict[str, Project] = {}

def _save_projects():
    try:
        data = {pid: p.model_dump() for pid, p in PROJECTS_DB.items()}
        with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Projects] 保存持久化项目失败: {e}")

def _load_projects():
    if PROJECTS_FILE.exists():
        try:
            with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for pid, pdata in data.items():
                    PROJECTS_DB[pid] = Project(**pdata)
        except Exception as e:
            print(f"[Projects] 加载持久化项目失败: {e}")

_load_projects()

# ==================== 0. AI 模型配置中心与连通性探针 ====================

@router.get("/ai/settings", summary="获取当前 AI 模型配置与可用预设")
def get_ai_settings():
    return ai_settings_manager.get_settings(mask_key=True)

@router.put("/ai/settings", summary="更新 AI 模型配置并热重载")
def update_ai_settings(settings_data: Dict[str, Any] = Body(...)):
    updated = ai_settings_manager.update_settings(settings_data)
    llm_client.reload_config()
    return {"status": "success", "settings": updated}

@router.post("/ai/test", summary="测试大模型端点连通性与延时")
def test_ai_connection(payload: Dict[str, Any] = Body(...)):
    res = ai_settings_manager.test_connection(
        api_key=payload.get("api_key"),
        base_url=payload.get("base_url"),
        model=payload.get("model")
    )
    return res

@router.post("/ai/models/fetch", summary="联网动态获取模型供应商的实时可用模型列表")
def fetch_ai_models(payload: Dict[str, Any] = Body(...)):
    res = ai_settings_manager.fetch_online_models(
        api_key=payload.get("api_key"),
        base_url=payload.get("base_url"),
        provider=payload.get("provider")
    )
    return res


# ==================== 1. 知识库模块 ====================

@router.post("/knowledge/upload", summary="上传并解析历史中标标书/白皮书")
async def upload_historical_bid(file: UploadFile = File(...)):
    if not file.filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="目前仅支持上传 .docx 格式文档")

    saved_path = settings.UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{file.filename}"
    content = await file.read()
    with open(saved_path, "wb") as f:
        f.write(content)

    try:
        parsed_doc = parser.parse_docx(saved_path)
        chunks = parser.flatten_sections_to_chunks(parsed_doc, doc_name=file.filename)
        added_count = knowledge_store.add_chunks(chunks)
        
        return {
            "status": "success",
            "filename": file.filename,
            "total_sections_parsed": parsed_doc["total_sections"],
            "chunks_indexed": added_count,
            "total_knowledge_chunks": len(knowledge_store.chunks)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解析标书文档失败: {str(e)}")

@router.get("/knowledge/stats", summary="获取知识库统计信息")
def get_knowledge_stats():
    chunks = knowledge_store.chunks
    docs = list({c["doc_name"] for c in chunks})
    tags = list({t for c in chunks for t in c.get("tags", [])})
    return {
        "total_chunks": len(chunks),
        "total_documents": len(docs),
        "document_list": docs,
        "available_tags": tags
    }

@router.post("/knowledge/search", summary="检索匹配的历史标书资产")
def search_knowledge(req: KnowledgeQueryRequest):
    results = knowledge_store.search(query=req.query, top_k=req.top_k, tag_filter=req.tag_filter)
    return {"results": results}

# ==================== 2. 招标文件 18 项结构化拆解 ====================

@router.post("/tender/analyze", response_model=TenderAnalysis18, summary="上传招标文件并执行18项核心要素拆解")
async def analyze_tender_document(file: UploadFile = File(...)):
    if not file.filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="目前仅支持上传 .docx 招标文件")

    saved_path = settings.UPLOAD_DIR / f"tender_{uuid.uuid4().hex[:8]}_{file.filename}"
    content = await file.read()
    with open(saved_path, "wb") as f:
        f.write(content)

    try:
        parsed_doc = parser.parse_docx(saved_path)
        all_text = "\n".join([sec.get("content", "") for sec in parsed_doc.get("sections", [])])
        analysis = tender_analyzer.analyze_text(all_text)
        if not analysis.project_name:
            analysis.project_name = file.filename.replace(".docx", "")
        return analysis
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"招标文件18项拆解失败: {str(e)}")

# ==================== 3. 项目与大纲管理 ====================

@router.get("/projects", response_model=List[ProjectListItem], summary="获取所有标书项目列表与编纂进度")
def list_projects():
    items = []
    for pid, p in PROJECTS_DB.items():
        total_sec = 0
        done_sec = 0
        def count_nodes(nodes):
            nonlocal total_sec, done_sec
            for n in nodes:
                total_sec += 1
                if n.status in ["completed", "reviewed"]:
                    done_sec += 1
                if n.children:
                    count_nodes(n.children)
        count_nodes(p.outline)
        rate = round((done_sec / total_sec * 100), 1) if total_sec else 0.0
        items.append(ProjectListItem(
            id=p.id,
            name=p.name,
            client_name=p.client_name,
            description=p.description,
            completion_rate=rate,
            section_count=total_sec,
            completed_sections=done_sec,
            created_at=p.created_at,
            updated_at=p.updated_at
        ))
    return items

@router.post("/project/create", response_model=Project, summary="新建标书项目并初始化大纲与全局事实")
def create_project(req: ProjectCreate):
    proj_id = f"proj_{uuid.uuid4().hex[:8]}"
    default_outline = bid_generator.generate_outline_from_rfp(req.description or req.name)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    project = Project(
        id=proj_id,
        name=req.name,
        client_name=req.client_name or "招标单位",
        description=req.description or "",
        facts=req.facts or GlobalFacts(),
        outline=default_outline,
        created_at=now_str,
        updated_at=now_str
    )
    PROJECTS_DB[proj_id] = project
    _save_projects()
    return project

@router.get("/project/{project_id}", response_model=Project, summary="获取项目详情与大纲树")
def get_project(project_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    return PROJECTS_DB[project_id]

@router.delete("/project/{project_id}", summary="删除指定标书项目")
def delete_project(project_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    del PROJECTS_DB[project_id]
    _save_projects()
    return {"status": "success", "deleted_id": project_id}

@router.put("/project/{project_id}/facts", summary="更新项目全局事实设定（Global Facts）")
def update_project_facts(project_id: str, facts: GlobalFacts):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    PROJECTS_DB[project_id].facts = facts
    PROJECTS_DB[project_id].updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_projects()
    return {"status": "success", "facts": facts}

# ==================== 4. 智能撰写与降AI味润色 ====================

def _find_and_update_node(nodes: List[OutlineNode], section_id: str, new_content: str, status: str = "completed") -> bool:
    for node in nodes:
        if node.id == section_id:
            node.content = new_content
            node.status = status
            return True
        if _find_and_update_node(node.children, section_id, new_content, status):
            return True
    return False

@router.post("/project/{project_id}/outline/generate-ai", summary="调用 AI 基于招标要求重新定制专属大纲")
def generate_project_outline_ai(project_id: str, rfp_summary: Optional[str] = Body(default="", embed=True)):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    project = PROJECTS_DB[project_id]
    summary_text = rfp_summary or (project.tender_analysis.model_dump_json() if project.tender_analysis else project.description) or project.name
    new_outline = bid_generator.generate_outline_from_rfp(summary_text)
    project.outline = new_outline
    project.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_projects()
    return {"status": "success", "outline": new_outline}

@router.post("/project/{project_id}/section/generate", response_model=GenerateSectionResponse, summary="基于知识库与全局事实分章智能草拟 (完整同步)")
def generate_section_content(project_id: str, req: GenerateSectionRequest):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    project = PROJECTS_DB[project_id]

    draft_result = bid_generator.draft_section(
        section_title=req.section_title,
        section_path=req.section_path,
        requirements=req.requirements,
        custom_instruction=req.custom_instruction or "",
        facts=project.facts
    )

    _find_and_update_node(project.outline, req.section_id, draft_result["generated_content"], status="completed")
    project.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_projects()

    return GenerateSectionResponse(
        section_id=req.section_id,
        generated_content=draft_result["generated_content"],
        reference_sources=draft_result["references"],
        tokens_used=len(draft_result["generated_content"])
    )

@router.post("/project/{project_id}/section/generate/stream", summary="基于知识库与全局事实分章流式草拟 (SSE 打字机)")
async def generate_section_content_stream(project_id: str, req: GenerateSectionRequest):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    project = PROJECTS_DB[project_id]

    def event_generator():
        full_content = []
        try:
            for token in bid_generator.draft_section_stream(
                section_title=req.section_title,
                section_path=req.section_path,
                requirements=req.requirements,
                custom_instruction=req.custom_instruction or "",
                facts=project.facts
            ):
                full_content.append(token)
                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        
        # 流式结束时自动保存完整内容
        complete_text = "".join(full_content)
        _find_and_update_node(project.outline, req.section_id, complete_text, status="completed")
        project.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save_projects()
        yield f"data: {json.dumps({'done': True, 'section_id': req.section_id}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.put("/project/{project_id}/section", summary="人工保存/润色章节正文")
def update_section_content(project_id: str, req: UpdateSectionRequest):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    project = PROJECTS_DB[project_id]
    updated = _find_and_update_node(project.outline, req.section_id, req.content, status="reviewed")
    if not updated:
        raise HTTPException(status_code=404, detail="未找到对应章节")
    
    project.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_projects()
    return {"status": "success", "section_id": req.section_id}

@router.post("/project/{project_id}/section/polish", response_model=PolishSectionResponse, summary="单章节深度降AI味与公文严肃化润色")
def polish_section(project_id: str, req: PolishSectionRequest):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    project = PROJECTS_DB[project_id]
    resp = quality_inspector.polish_section(req, project.facts)
    _find_and_update_node(project.outline, req.section_id, resp.polished_content, status="reviewed")
    project.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_projects()
    return resp

# ==================== 5. 技术偏离表工作台 ====================

@router.post("/project/{project_id}/deviation/extract", summary="从招标文件提取技术指标并构建偏离表框架")
def extract_project_deviations(project_id: str, tender_content: Optional[str] = Body(default="", embed=True)):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    project = PROJECTS_DB[project_id]
    raw_text = tender_content or project.description or project.name
    extracted_items = deviation_engine.extract_requirements(raw_text)
    project.deviation_matrix = extracted_items
    project.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_projects()
    return {"status": "success", "total_items": len(extracted_items), "items": extracted_items}

@router.get("/project/{project_id}/deviation", summary="获取项目当前技术偏离表")
def get_project_deviations(project_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"items": PROJECTS_DB[project_id].deviation_matrix}

@router.post("/project/{project_id}/deviation/generate", summary="智能批量生成点对点技术响应并自动回填大纲")
def generate_project_deviations(project_id: str, auto_inject_section_id: Optional[str] = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    project = PROJECTS_DB[project_id]
    if not project.deviation_matrix:
        project.deviation_matrix = deviation_engine.extract_requirements(project.description)

    # 批量生成点对点响应
    answered_items = deviation_engine.batch_generate_responses(project.deviation_matrix, project.facts)
    project.deviation_matrix = answered_items

    # 转换为精美 Markdown 表格
    deviation_table_md = deviation_engine.to_markdown_table(answered_items)

    # 若指定了目标章节（例如 第四章 或 技术偏离表），自动回填
    if auto_inject_section_id:
        _find_and_update_node(project.outline, auto_inject_section_id, deviation_table_md, status="completed")

    project.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_projects()

    return {
        "status": "success",
        "total_items": len(answered_items),
        "items": answered_items,
        "table_markdown": deviation_table_md
    }

# ==================== 6. 18项拆标联动与同步 ====================

@router.post("/project/{project_id}/tender/sync-to-facts", summary="将18项拆标结果一键同步到项目全局事实")
def sync_tender_to_facts(project_id: str, analysis: TenderAnalysis18):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    project = PROJECTS_DB[project_id]
    project.tender_analysis = analysis
    if analysis.purchaser_name and (not project.client_name or project.client_name == "招标单位"):
        project.client_name = analysis.purchaser_name
    if analysis.duration_requirement:
        project.facts.delivery_guarantee = f"承诺在【{analysis.duration_requirement}】内保质完成整体交付"
    if analysis.warranty_period:
        project.facts.sla_commitment = f"承诺提供【{analysis.warranty_period}】及7×24小时全天候响应保障"
    project.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_projects()
    return {"status": "success", "facts": project.facts}

@router.post("/project/{project_id}/tender/sync-to-deviations", summary="将18项拆标的★号与硬性条款同步到技术偏离表")
def sync_tender_to_deviations(project_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    project = PROJECTS_DB[project_id]
    if not project.tender_analysis or not project.tender_analysis.star_disqualification_items:
        raise HTTPException(status_code=400, detail="该项目尚未提取到★号条款，请先在拆解台分析招标文件")
    
    new_items = []
    idx = len(project.deviation_matrix) + 1
    for star in project.tender_analysis.star_disqualification_items:
        new_items.append(DeviationItem(
            index=idx,
            clause_title=star,
            is_star=True,
            response_status="完全满足",
            response_detail=""
        ))
        idx += 1
    project.deviation_matrix.extend(new_items)
    project.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_projects()
    return {"status": "success", "total_items": len(project.deviation_matrix), "added_count": len(new_items)}

# ==================== 7. 合规体检与八维质检模块 ====================

@router.post("/project/{project_id}/compliance/check", response_model=ComplianceCheckReport, summary="执行标书废标项与负偏离合规审查")
def run_compliance_check(project_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    project = PROJECTS_DB[project_id]
    star_items = []
    if project.deviation_matrix:
        star_items = [it.clause_title for it in project.deviation_matrix if it.is_star]
    
    if not star_items and project.tender_analysis and project.tender_analysis.star_disqualification_items:
        star_items = project.tender_analysis.star_disqualification_items
    
    if not star_items:
        star_items = [
            "★ 投标人所投软件系统必须具备自主知识产权与软件著作权。",
            "★ 系统必须支持国家网络安全等级保护（三级）标准要求。",
            "★ 核心数据存储必须支持国产密码算法（SM2/SM3/SM4）加密。"
        ]

    report = compliance_checker.check_compliance(
        star_items=star_items,
        outline=project.outline,
        facts=project.facts
    )
    return report

@router.post("/project/{project_id}/quality/inspect", response_model=EightDimensionQualityReport, summary="执行标书八维全盘质量体检")
def run_eight_dimension_quality_audit(project_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    project = PROJECTS_DB[project_id]
    star_items = [it.clause_title for it in project.deviation_matrix if it.is_star]
    if not star_items and project.tender_analysis:
        star_items = project.tender_analysis.star_disqualification_items
    report = quality_inspector.inspect_quality(project.outline, project.facts, star_items)
    return report

# ==================== 8. 企业中台集中资产库 (借鉴 Yibiao-Web) ====================

@router.get("/assets/stats", summary="获取企业资产中台统计概览")
def get_assets_stats():
    return asset_manager.get_stats()

@router.get("/assets/qualifications", response_model=List[CompanyQualification], summary="查询企业资质认证列表")
def list_company_qualifications(category: Optional[str] = None, search: Optional[str] = None):
    return asset_manager.list_qualifications(category, search)

@router.post("/assets/qualifications", response_model=CompanyQualification, summary="新增或更新企业资质认证")
def add_company_qualification(qual: CompanyQualification):
    return asset_manager.add_qualification(qual)

@router.delete("/assets/qualifications/{qual_id}", summary="删除企业资质认证")
def delete_company_qualification(qual_id: str):
    ok = asset_manager.delete_qualification(qual_id)
    if not ok:
        raise HTTPException(status_code=404, detail="资质认证不存在")
    return {"status": "success", "deleted_id": qual_id}

@router.get("/assets/personnel", response_model=List[PersonnelAsset], summary="查询企业核心技术骨干人员列表")
def list_personnel_assets(role: Optional[str] = None, search: Optional[str] = None):
    return asset_manager.list_personnel(role, search)

@router.post("/assets/personnel", response_model=PersonnelAsset, summary="新增或更新人员证书履历")
def add_personnel_asset(person: PersonnelAsset):
    return asset_manager.add_personnel(person)

@router.delete("/assets/personnel/{person_id}", summary="删除人员证书履历")
def delete_personnel_asset(person_id: str):
    ok = asset_manager.delete_personnel(person_id)
    if not ok:
        raise HTTPException(status_code=404, detail="人员记录不存在")
    return {"status": "success", "deleted_id": person_id}

@router.get("/assets/cases", response_model=List[CaseContract], summary="查询企业同类中标业绩案例列表")
def list_case_contracts(category: Optional[str] = None, search: Optional[str] = None):
    return asset_manager.list_cases(category, search)

@router.post("/assets/cases", response_model=CaseContract, summary="新增或更新业绩合同案例")
def add_case_contract(case_item: CaseContract):
    return asset_manager.add_case(case_item)

@router.delete("/assets/cases/{case_id}", summary="删除业绩合同案例")
def delete_case_contract(case_id: str):
    ok = asset_manager.delete_case(case_id)
    if not ok:
        raise HTTPException(status_code=404, detail="案例不存在")
    return {"status": "success", "deleted_id": case_id}

@router.get("/assets/components", response_model=List[SolutionComponent], summary="查询企业标准方案组件列表")
def list_solution_components(category: Optional[str] = None, search: Optional[str] = None):
    return asset_manager.list_components(category, search)

@router.post("/assets/components", response_model=SolutionComponent, summary="新增或更新方案组件")
def add_solution_component(comp: SolutionComponent):
    return asset_manager.add_component(comp)

@router.delete("/assets/components/{comp_id}", summary="删除方案组件")
def delete_solution_component(comp_id: str):
    ok = asset_manager.delete_component(comp_id)
    if not ok:
        raise HTTPException(status_code=404, detail="组件不存在")
    return {"status": "success", "deleted_id": comp_id}

# ==================== 9. 高保真导出与模板管理 ====================

@router.get("/templates/list", summary="获取可用的标书 Word 格式排版模板")
def list_word_templates():
    return {"templates": template_manager.list_templates()}

@router.post("/templates/create", summary="创建或更新自定义标书 Word 模板")
def create_custom_template(template_data: Dict[str, Any]):
    saved = template_manager.create_or_update_template(template_data)
    return {"status": "success", "template": saved}

@router.get("/project/{project_id}/export", summary="导出项目为高保真技术标书 Word")
def export_project_bid(project_id: str, template_id: Optional[str] = "gov_standard"):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    project = PROJECTS_DB[project_id]
    style_cfg = template_manager.get_template(template_id or "gov_standard")
    
    out_path = docx_exporter.export_project_to_docx(
        project_name=project.name,
        client_name=project.client_name,
        outline=project.outline,
        facts=project.facts,
        style_cfg=style_cfg
    )
    
    return FileResponse(
        path=str(out_path),
        filename=out_path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
