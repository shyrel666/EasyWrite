"""企业资产中台路由（资质/人员/业绩/组件 CRUD、证明附件、证明材料检查）"""
from contextlib import contextmanager
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.models.schemas import (
    AssetAttachment, CompanyQualification, PersonnelAsset, CaseContract, SolutionComponent,
)
from app.services.assets.asset_manager import MAX_ATTACHMENT_BYTES, AttachmentError, asset_manager
from app.services.assets.material_check import bid_deadline
from app.services.project_store import project_store

router = APIRouter(prefix="/assets", tags=["企业资产中台"])


@router.get("/stats", summary="获取企业资产中台统计概览")
def get_assets_stats():
    return asset_manager.get_stats()


@router.get("/material-check", summary="证明材料检查：资质/人员/业绩逐条核对附件、有效期、所属主体与核实状态")
def check_asset_materials(project_id: Optional[str] = None):
    """传 project_id 时以该项目的投标截止时间与投标人全称为准；否则以当天为准、不核对所属主体"""
    facts, analysis = None, None
    if project_id:
        project = project_store.get(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        facts, analysis = project.facts, project.tender_analysis
    deadline, source = bid_deadline(analysis)
    return {
        "reference_date": deadline.isoformat(),
        "reference_source": source,
        "company_name": facts.company_name if facts else "",
        "checks": asset_manager.check_materials(facts, deadline),
    }


# ---------------- 证明附件 ----------------

@contextmanager
def _attachment_errors(missing: str = "资料不存在"):
    """附件操作的错误映射：类型/大小不合法 → 400，资料或附件不存在 → 404"""
    try:
        yield
    except AttachmentError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except KeyError as e:
        raise HTTPException(status_code=404, detail=missing) from e


@router.get("/{kind}/{asset_id}/attachments", response_model=List[AssetAttachment], summary="列出资料的证明附件")
def list_asset_attachments(kind: str, asset_id: str):
    with _attachment_errors():
        return asset_manager.list_attachments(kind, asset_id)


@router.post("/{kind}/{asset_id}/attachments", response_model=AssetAttachment, summary="上传证明附件（PDF / 图片，单个不超过 20MB）")
async def upload_asset_attachment(kind: str, asset_id: str, file: UploadFile = File(...)):
    # 分块读取，超过上限立即停止，不把超大文件整体读进内存
    chunks, size = [], 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > MAX_ATTACHMENT_BYTES:
            raise HTTPException(status_code=400, detail="单个附件不能超过 20MB")
        chunks.append(chunk)
    with _attachment_errors():
        return asset_manager.add_attachment(kind, asset_id, file.filename or "", b"".join(chunks))


@router.get("/{kind}/{asset_id}/attachments/{file_id}", summary="下载/预览证明附件")
def download_asset_attachment(kind: str, asset_id: str, file_id: str):
    with _attachment_errors("附件不存在"):
        path, meta = asset_manager.attachment_file(kind, asset_id, file_id)
    # inline：浏览器内直接预览 PDF / 图片
    return FileResponse(path=str(path), filename=meta.filename, media_type=meta.content_type,
                        content_disposition_type="inline")


@router.delete("/{kind}/{asset_id}/attachments/{file_id}", summary="删除证明附件")
def delete_asset_attachment(kind: str, asset_id: str, file_id: str):
    with _attachment_errors():
        deleted = asset_manager.delete_attachment(kind, asset_id, file_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="附件不存在")
    return {"status": "success", "deleted_id": file_id}


@router.delete("/examples", summary="清除全部预设示例资料（四类资料中 status=example 的条目）")
def clear_example_assets():
    return {"status": "success", "removed": asset_manager.clear_examples()}


@router.get("/qualifications", response_model=List[CompanyQualification], summary="查询企业资质认证列表")
def list_company_qualifications(category: Optional[str] = None, search: Optional[str] = None):
    return asset_manager.list_qualifications(category, search)


@router.post("/qualifications", response_model=CompanyQualification, summary="新增或更新企业资质认证")
def add_company_qualification(qual: CompanyQualification):
    return asset_manager.add_qualification(qual)


@router.delete("/qualifications/{qual_id}", summary="删除企业资质认证")
def delete_company_qualification(qual_id: str):
    if not asset_manager.delete_qualification(qual_id):
        raise HTTPException(status_code=404, detail="资质认证不存在")
    return {"status": "success", "deleted_id": qual_id}


@router.get("/personnel", response_model=List[PersonnelAsset], summary="查询企业核心技术骨干人员列表")
def list_personnel_assets(role: Optional[str] = None, search: Optional[str] = None):
    return asset_manager.list_personnel(role, search)


@router.post("/personnel", response_model=PersonnelAsset, summary="新增或更新人员证书履历")
def add_personnel_asset(person: PersonnelAsset):
    return asset_manager.add_personnel(person)


@router.delete("/personnel/{person_id}", summary="删除人员证书履历")
def delete_personnel_asset(person_id: str):
    if not asset_manager.delete_personnel(person_id):
        raise HTTPException(status_code=404, detail="人员记录不存在")
    return {"status": "success", "deleted_id": person_id}


@router.get("/cases", response_model=List[CaseContract], summary="查询企业同类中标业绩案例列表")
def list_case_contracts(category: Optional[str] = None, search: Optional[str] = None):
    return asset_manager.list_cases(category, search)


@router.post("/cases", response_model=CaseContract, summary="新增或更新业绩合同案例")
def add_case_contract(case_item: CaseContract):
    return asset_manager.add_case(case_item)


@router.delete("/cases/{case_id}", summary="删除业绩合同案例")
def delete_case_contract(case_id: str):
    if not asset_manager.delete_case(case_id):
        raise HTTPException(status_code=404, detail="案例不存在")
    return {"status": "success", "deleted_id": case_id}


@router.get("/components", response_model=List[SolutionComponent], summary="查询企业标准方案组件列表")
def list_solution_components(category: Optional[str] = None, search: Optional[str] = None):
    return asset_manager.list_components(category, search)


@router.post("/components", response_model=SolutionComponent, summary="新增或更新方案组件")
def add_solution_component(comp: SolutionComponent):
    return asset_manager.add_component(comp)


@router.delete("/components/{comp_id}", summary="删除方案组件")
def delete_solution_component(comp_id: str):
    if not asset_manager.delete_component(comp_id):
        raise HTTPException(status_code=404, detail="组件不存在")
    return {"status": "success", "deleted_id": comp_id}
