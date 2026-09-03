from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime

class OutlineNode(BaseModel):
    id: str = Field(..., description="章节唯一ID，如 sec_1_2")
    title: str = Field(..., description="章节标题，如 1.2 建设目标")
    level: int = Field(default=1, description="层级，1表示一级标题，2表示二级标题，3表示三级标题")
    path: str = Field(default="", description="完整面包屑路径，如 总体架构 > 软件架构 > 微服务设计")
    content: str = Field(default="", description="该章节已生成的正文内容（支持Markdown/多段落）")
    requirements: List[str] = Field(default_factory=list, description="本章节需要响应的招标文件要求/评分点")
    status: str = Field(default="pending", description="状态: pending, generating, completed, reviewed")
    children: List['OutlineNode'] = Field(default_factory=list, description="子章节节点")

OutlineNode.model_rebuild()

class GlobalFacts(BaseModel):
    """
    全局事实设定（借鉴自 OpenBidKit / YuduBid）：
    在整本长文标书编撰中作为强制上下文只读约束，彻底防止前后公司名、产品线、技术指标不一致。
    """
    company_name: str = Field(default="标书联合体（默认供应商）", description="投标企业法定全称")
    credit_code: str = Field(default="91110108MA00000000", description="统一社会信用代码")
    legal_rep: str = Field(default="张三", description="法定代表人姓名")
    registered_capital: str = Field(default="5000万元人民币", description="注册资本")
    core_product_name: str = Field(default="EasyPlatform 企业数智化底座", description="本次投标主打核心产品/平台")
    architecture_stack: str = Field(default="云原生微服务 (Spring Cloud + Vue 3) 容器化架构", description="标准技术路线栈")
    database_selection: str = Field(default="国产信创达梦数据库 / PostgreSQL 混合部署", description="指定数据库选型")
    sla_commitment: str = Field(default="7×24小时技术支持，5分钟内响应，30分钟内远程支持，2小时内到达现场", description="服务响应承诺标准")
    delivery_guarantee: str = Field(default="承诺在合同签署后 90 个日历日内保质完成整体系统交付与试运行", description="工期承诺")
    custom_facts: Dict[str, str] = Field(default_factory=dict, description="其他用户自定义的特定只读事实")

    def to_constraint_text(self) -> str:
        """将全局事实转换为大模型 System Prompt 约束文本"""
        lines = [
            f"- 投标人官方全称：{self.company_name}",
            f"- 统一社会信用代码：{self.credit_code}",
            f"- 法定代表人：{self.legal_rep}",
            f"- 注册资本：{self.registered_capital}",
            f"- 投标核心产品平台：{self.core_product_name}",
            f"- 统一技术架构路线：{self.architecture_stack}",
            f"- 统一数据库底座：{self.database_selection}",
            f"- 统一SLA售后承诺：{self.sla_commitment}",
            f"- 统一工期承诺：{self.delivery_guarantee}",
        ]
        for k, v in self.custom_facts.items():
            lines.append(f"- {k}：{v}")
        return "\n".join(lines)

class TenderAnalysis18(BaseModel):
    """
    招标文件 18 项结构化拆解模型（借鉴 OpenBidKit 18 项要素）
    """
    project_name: str = Field(default="", description="1. 招标项目名称")
    tender_number: str = Field(default="", description="2. 招标项目编号/标段号")
    purchaser_name: str = Field(default="", description="3. 采购人/招标代理机构")
    budget_limit: str = Field(default="", description="4. 招标预算或最高投标限价")
    duration_requirement: str = Field(default="", description="5. 要求的项目工期/交付期限")
    warranty_period: str = Field(default="", description="6. 免费质保与售后服务期要求")
    bid_security: str = Field(default="", description="7. 投标保证金金额与递交方式")
    bid_validity_period: str = Field(default="", description="8. 投标有效期天数")
    scoring_method: str = Field(default="综合评分法", description="9. 评标方法 (综合评分法/最低评标价法)")
    price_score_weight: str = Field(default="30分", description="10. 价格分权重与评分公式")
    tech_score_weight: str = Field(default="50分", description="11. 技术方案分值与各章节评分明细")
    business_score_weight: str = Field(default="20分", description="12. 商务资信分值与资质要求")
    star_disqualification_items: List[str] = Field(default_factory=list, description="13. ★号不可偏离重大条款与废标红线清单")
    qualification_thresholds: List[str] = Field(default_factory=list, description="14. 投标人强制资质门槛（如涉密、CMMI、ISO等）")
    project_location: str = Field(default="", description="15. 项目实施与交付地点")
    payment_milestones: str = Field(default="", description="16. 付款节点比例 (如 3:3:3:1)")
    site_survey_rules: str = Field(default="", description="17. 是否组织现场踏勘及答疑规则")
    submission_deadline: str = Field(default="", description="18. 投标截止时间与递交形式")

class DeviationItem(BaseModel):
    """技术规格与条款点对点偏离表项"""
    index: int
    clause_title: str = Field(..., description="招标文件条款要求")
    is_star: bool = Field(default=False, description="是否为★号不可偏离条款")
    response_status: str = Field(default="完全满足", description="响应结果：完全满足/正偏离/负偏离")
    response_detail: str = Field(default="", description="点对点具体实现技术论述与佐证")

class ComplianceCheckReport(BaseModel):
    """标书废标项与合规体检报告"""
    passed: bool = Field(..., description="是否通过合规检查")
    total_star_items: int = Field(default=0, description="★号废标条款总数")
    satisfied_star_items: int = Field(default=0, description="已满足★号条款数")
    risk_items: List[Dict[str, str]] = Field(default_factory=list, description="高危/未满足/负偏离风险项")
    warnings: List[str] = Field(default_factory=list, description="建议优化的轻度风险提醒")
    summary: str = Field(default="", description="合规审查总体结论")

class ProjectCreate(BaseModel):
    name: str = Field(..., description="项目/标书名称")
    client_name: Optional[str] = Field(default="", description="客户/招标方名称")
    description: Optional[str] = Field(default="", description="项目简要背景描述")
    facts: Optional[GlobalFacts] = Field(default_factory=GlobalFacts, description="该项目的全局事实约束")

class Project(BaseModel):
    id: str
    name: str
    client_name: str
    description: str
    facts: GlobalFacts = Field(default_factory=GlobalFacts)
    tender_analysis: Optional[TenderAnalysis18] = None
    deviation_matrix: List[DeviationItem] = Field(default_factory=list)
    outline: List[OutlineNode]
    created_at: str
    updated_at: str

class KnowledgeChunk(BaseModel):
    id: str
    doc_name: str = Field(..., description="来源文件名")
    section_title: str = Field(..., description="所属章节标题")
    breadcrumb: str = Field(..., description="大纲完整面包屑路径")
    content: str = Field(..., description="切片正文")
    tags: List[str] = Field(default_factory=list, description="自动提取的技术或业务标签")
    score: Optional[float] = Field(default=0.0, description="相似度分数")

class KnowledgeQueryRequest(BaseModel):
    query: str = Field(..., description="检索问题或章节需求")
    top_k: int = Field(default=5, description="召回数量")
    tag_filter: Optional[List[str]] = Field(default=None, description="标签过滤")

class GenerateSectionRequest(BaseModel):
    project_id: str
    section_id: str
    section_title: str
    section_path: str = ""
    requirements: List[str] = Field(default_factory=list)
    custom_instruction: Optional[str] = Field(default="", description="人工补充的特殊要求或提示词")

class GenerateSectionResponse(BaseModel):
    section_id: str
    generated_content: str
    reference_sources: List[KnowledgeChunk]
    tokens_used: int = 0

class UpdateSectionRequest(BaseModel):
    section_id: str
    content: str
