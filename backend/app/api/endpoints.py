import os
import uuid
from typing import List, Dict, Any, Optional
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks, Body
from fastapi.responses import FileResponse

from app.core.config import settings
from app.models.schemas import (
    OutlineNode, Project, ProjectCreate, GlobalFacts,
    KnowledgeChunk, KnowledgeQueryRequest,
    GenerateSectionRequest, GenerateSectionResponse,
    UpdateSectionRequest, TenderAnalysis18, ComplianceCheckReport,
    DeviationItem
)
from app.services.parser.word_parser import WordDocumentParser
from app.services.parser.tender_analyzer import tender_analyzer
from app.services.parser.deviation_engine import deviation_engine
from app.services.rag.vector_store import knowledge_store
from app.services.generator.bid_generator import bid_generator
from app.services.checker.compliance_checker import compliance_checker
from app.services.exporter.docx_generator import docx_exporter
from app.services.exporter.template_manager import template_manager

router = APIRouter()
parser = WordDocumentParser()

# 内存中的项目状态存储（支持后续无缝替换为 PostgreSQL / SQLite）
PROJECTS_DB: Dict[str, Project] = {}

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

@router.post("/project/create", response_model=Project, summary="新建标书项目并初始化大纲与全局事实")
def create_project(req: ProjectCreate):
    proj_id = f"proj_{uuid.uuid4().hex[:8]}"
    default_outline = bid_generator.generate_outline_from_rfp(req.description or req.name)
    
    project = Project(
        id=proj_id,
        name=req.name,
        client_name=req.client_name or "招标单位",
        description=req.description or "",
        facts=req.facts or GlobalFacts(),
        outline=default_outline,
        created_at="2026-09-03 14:30:00",
        updated_at="2026-09-03 14:30:00"
    )
    PROJECTS_DB[proj_id] = project
    return project

@router.get("/project/{project_id}", response_model=Project, summary="获取项目详情与大纲树")
def get_project(project_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    return PROJECTS_DB[project_id]

@router.put("/project/{project_id}/facts", summary="更新项目全局事实设定（Global Facts）")
def update_project_facts(project_id: str, facts: GlobalFacts):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    PROJECTS_DB[project_id].facts = facts
    return {"status": "success", "facts": facts}

# ==================== 4. 智能撰写模块 ====================

def _find_and_update_node(nodes: List[OutlineNode], section_id: str, new_content: str, status: str = "completed") -> bool:
    for node in nodes:
        if node.id == section_id:
            node.content = new_content
            node.status = status
            return True
        if _find_and_update_node(node.children, section_id, new_content, status):
            return True
    return False

@router.post("/project/{project_id}/section/generate", response_model=GenerateSectionResponse, summary="基于知识库与全局事实分章智能草拟")
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

    return GenerateSectionResponse(
        section_id=req.section_id,
        generated_content=draft_result["generated_content"],
        reference_sources=draft_result["references"],
        tokens_used=len(draft_result["generated_content"])
    )

@router.put("/project/{project_id}/section", summary="人工保存/润色章节正文")
def update_section_content(project_id: str, req: UpdateSectionRequest):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    project = PROJECTS_DB[project_id]
    updated = _find_and_update_node(project.outline, req.section_id, req.content, status="reviewed")
    if not updated:
        raise HTTPException(status_code=404, detail="未找到对应章节")
    
    return {"status": "success", "section_id": req.section_id}

# ==================== 5. 技术偏离表工作台 ====================

@router.post("/project/{project_id}/deviation/extract", summary="从招标文件提取技术指标并构建偏离表框架")
def extract_project_deviations(project_id: str, tender_content: Optional[str] = Body(default="", embed=True)):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    project = PROJECTS_DB[project_id]
    raw_text = tender_content or project.description or project.name
    extracted_items = deviation_engine.extract_requirements(raw_text)
    project.deviation_matrix = extracted_items
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

    return {
        "status": "success",
        "total_items": len(answered_items),
        "items": answered_items,
        "table_markdown": deviation_table_md
    }

# ==================== 6. 废标项合规检查模块 ====================

@router.post("/project/{project_id}/compliance/check", response_model=ComplianceCheckReport, summary="执行标书废标项与负偏离合规审查")
def run_compliance_check(project_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    project = PROJECTS_DB[project_id]
    
    # 提取已有的★号条款
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

# ==================== 7. 高保真导出与模板管理 ====================

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
