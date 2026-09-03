"""企业资产中台路由（资质/人员/业绩/组件 CRUD）"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    CompanyQualification, PersonnelAsset, CaseContract, SolutionComponent,
)
from app.services.assets.asset_manager import asset_manager

router = APIRouter(prefix="/assets", tags=["企业资产中台"])


@router.get("/stats", summary="获取企业资产中台统计概览")
def get_assets_stats():
    return asset_manager.get_stats()


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
