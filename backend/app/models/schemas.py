from typing import List, Literal, Optional, Dict, Any
from pydantic import BaseModel, Field

# 哨兵值：字段未在招标文件中出现时的显式占位（绝不静默编造默认值）
NOT_MENTIONED = "未提及"


class OutlineNode(BaseModel):
    id: str = Field(..., description="章节唯一ID，如 sec_1_2")
    title: str = Field(..., description="章节标题，如 1.2 建设目标")
    level: int = Field(default=1, description="层级，1表示一级标题，2表示二级标题，3表示三级标题")
    path: str = Field(default="", description="完整面包屑路径，如 总体架构 > 软件架构 > 微服务设计")
    content: str = Field(default="", description="该章节已生成的正文内容（支持Markdown/多段落）")
    requirements: List[str] = Field(default_factory=list, description="本章节需要响应的招标文件要求/评分点")
    status: str = Field(default="pending", description="状态: pending, generating, completed, reviewed")
    # 字数预算（评分项对齐时按评分比重分配）
    word_budget: Optional[int] = Field(default=None, description="目标正文字数")
    # 内容模式：ai_generate=AI撰写 / point_to_point=点对点应答表 / template_fill=模板填充
    content_mode: str = Field(default="ai_generate")
    # 引用溯源（人工在环）：锁定的知识库块在重新生成时必注入；排除的永不注入
    pinned_refs: List[str] = Field(default_factory=list, description="锁定引用的 chunk_id 列表")
    excluded_refs: List[str] = Field(default_factory=list, description="排除引用的 chunk_id 列表")
    # 最近一次生成使用的引用（溯源展示）
    last_refs: List[Dict[str, Any]] = Field(default_factory=list)
    # 本章节承接的评分项（ScoringItem.id），用于字数分配与评分点覆盖核查
    scoring_item_ids: List[str] = Field(default_factory=list)
    children: List['OutlineNode'] = Field(default_factory=list, description="子章节节点")


OutlineNode.model_rebuild()


class GlobalFacts(BaseModel):
    """
    全局事实设定（借鉴 OpenBidKit / YuduBid）：
    在整本长文标书编撰中作为强制上下文只读约束，彻底防止前后公司名、产品线、技术指标不一致。
    """
    company_name: str = Field(default="", description="投标企业法定全称")
    credit_code: str = Field(default="", description="统一社会信用代码")
    legal_rep: str = Field(default="", description="法定代表人姓名")
    registered_capital: str = Field(default="", description="注册资本")
    core_product_name: str = Field(default="", description="本次投标主打核心产品/平台")
    architecture_stack: str = Field(default="", description="标准技术路线栈")
    database_selection: str = Field(default="", description="指定数据库选型")
    sla_commitment: str = Field(default="", description="服务响应承诺标准")
    delivery_guarantee: str = Field(default="", description="工期承诺")
    # 事实完备模式：omit=未提供的事实保持模糊不编造 / placeholder=正文输出【待填写】占位
    fact_completeness_mode: str = Field(default="placeholder", description="omit | placeholder")
    custom_facts: Dict[str, str] = Field(default_factory=dict, description="其他用户自定义的特定只读事实")

    def to_constraint_text(self) -> str:
        """将全局事实转换为大模型 System Prompt 约束文本"""
        filled = []
        fields = [
            ("投标人官方全称", self.company_name),
            ("统一社会信用代码", self.credit_code),
            ("法定代表人", self.legal_rep),
            ("注册资本", self.registered_capital),
            ("投标核心产品平台", self.core_product_name),
            ("统一技术架构路线", self.architecture_stack),
            ("统一数据库底座", self.database_selection),
            ("统一SLA售后承诺", self.sla_commitment),
            ("统一工期承诺", self.delivery_guarantee),
        ]
        for label, value in fields:
            if value and value.strip() and value.strip() != NOT_MENTIONED:
                filled.append(f"- {label}：{value.strip()}")
        for k, v in self.custom_facts.items():
            if v and v.strip():
                filled.append(f"- {k}：{v.strip()}")

        lines = ["【全局事实硬约束（全文必须严格一致，禁止前后矛盾）】"]
        if filled:
            lines.extend(filled)
        if self.fact_completeness_mode == "placeholder":
            lines.append(
                "【事实完备纪律】以上未列出的事实（如具体金额、日期、人员数量）不得编造，"
                "如确需提及，一律以【待填写】占位，由人工后续核实填充。"
            )
        else:
            lines.append(
                "【事实完备纪律】以上未列出的事实一律保持模糊表述（如\"按合同约定\"），严禁编造具体数值。"
            )
        return "\n".join(lines)

    def filled_count(self) -> int:
        core = [self.company_name, self.credit_code, self.legal_rep, self.registered_capital,
                self.core_product_name, self.architecture_stack, self.database_selection,
                self.sla_commitment, self.delivery_guarantee]
        return sum(1 for v in core if v and v.strip() and v.strip() != NOT_MENTIONED)


class ScoringSubItem(BaseModel):
    """评分标准正文中显式给出分值的子项，如 "1.现状及需求分析（10分）" """
    name: str
    points: Optional[float] = None


class ScoringItem(BaseModel):
    """评分细则中的一个评分项（评分表的一行）——大纲对齐与评分点覆盖核查的基础"""
    id: str = Field(..., description="评分项 ID，如 pkg1_s3")
    package: str = Field(default="", description="分包标识，如 包1；单包项目为空")
    category: str = Field(default="", description="评分大类原文，如 技术部分（60%）")
    category_weight: Optional[float] = Field(default=None, description="评分大类权重（%）")
    kind: str = Field(default="other", description="price 价格 / technical 技术 / business 商务 / other")
    # 应答方式决定大纲如何承接：proposal 撰写方案章节 / evidence 证书业绩人员等证明材料 /
    # demo 现场演示 / compliance 逐条响应（起评分扣分制）/ price 报价
    response_type: str = Field(default="proposal")
    name: str = Field(..., description="评分项名称，如 技术服务方案")
    points: Optional[float] = Field(default=None, description="分值；无法识别时为空，不臆测")
    criteria: str = Field(default="", description="评分标准原文")
    note: str = Field(default="", description="说明/证明材料要求原文")
    sub_items: List[ScoringSubItem] = Field(default_factory=list)
    source: str = Field(default="table", description="table 评分表确定性抽取 / llm 大模型抽取 / manual 人工录入")


class TenderAnalysis18(BaseModel):
    """
    招标文件 18 项结构化拆解模型（借鉴 OpenBidKit 18 项要素）。
    未提取到的字段为空串或哨兵值 NOT_MENTIONED，绝无编造默认值。
    """
    project_name: str = Field(default="", description="1. 招标项目名称")
    tender_number: str = Field(default="", description="2. 招标项目编号/标段号")
    purchaser_name: str = Field(default="", description="3. 采购人/招标代理机构")
    budget_limit: str = Field(default="", description="4. 招标预算或最高投标限价")
    duration_requirement: str = Field(default="", description="5. 要求的项目工期/交付期限")
    warranty_period: str = Field(default="", description="6. 免费质保与售后服务期要求")
    bid_security: str = Field(default="", description="7. 投标保证金金额与递交方式")
    bid_validity_period: str = Field(default="", description="8. 投标有效期天数")
    scoring_method: str = Field(default="", description="9. 评标方法 (综合评分法/最低评标价法)")
    price_score_weight: str = Field(default="", description="10. 价格分权重与评分公式")
    tech_score_weight: str = Field(default="", description="11. 技术方案分值与各章节评分明细")
    business_score_weight: str = Field(default="", description="12. 商务资信分值与资质要求")
    star_disqualification_items: List[str] = Field(default_factory=list, description="13. 废标红线：不满足即无效投标的标记条款（★/※ 等，以文件对标记的定义为准；原文摘录）")
    important_items: List[str] = Field(default_factory=list, description="重要条款：不满足按评分办法扣分、不直接废标的标记条款（如重庆模板中的 ★/▲）")
    marker_legend: Dict[str, str] = Field(default_factory=dict, description="文件对条款标记含义的原文定义，如 {'※': '…不满足按无效投标处理'}")
    qualification_thresholds: List[str] = Field(default_factory=list, description="14. 投标人强制资质门槛")
    project_location: str = Field(default="", description="15. 项目实施与交付地点")
    payment_milestones: str = Field(default="", description="16. 付款节点比例")
    site_survey_rules: str = Field(default="", description="17. 是否组织现场踏勘及答疑规则")
    submission_deadline: str = Field(default="", description="18. 投标截止时间与递交形式")
    # 结构化评分细则（评分表逐项）+ 抽取说明（分值合计核对 / 未识别到评分表等）
    scoring_items: List[ScoringItem] = Field(default_factory=list, description="评分细则逐项")
    scoring_note: str = Field(default="", description="评分细则抽取说明")
    target_package: str = Field(default="", description="多分包项目中本次投标的分包（对应 ScoringItem.package）")
    # 诚实信号：llm=大模型抽取 / rules=正则规则抽取（覆盖度有限）
    extraction_mode: str = Field(default="llm")
    extraction_note: str = Field(default="")
    # 来源说明：PDF 页数、章节识别方式（书签 / 版面推断）、扫描页告警；Word 为空
    source_note: str = Field(default="")


class CommitmentSuggestion(BaseModel):
    """拆标得到的承诺建议：措辞只复述招标要求；经用户采纳后才写入全局事实"""
    field: str = Field(..., description="目标全局事实字段，如 delivery_guarantee")
    field_label: str = Field(default="", description="目标事实名称")
    source_field: str = Field(default="", description="来源拆标字段，如 duration_requirement")
    requirement: str = Field(..., description="招标文件中的要求（拆标结果原文）")
    suggestion: str = Field(..., description="建议写入全局事实的措辞")


class DeviationItem(BaseModel):
    """技术规格与条款点对点偏离表项"""
    index: int
    clause_title: str = Field(..., description="招标文件条款要求（原文摘录）")
    is_star: bool = Field(default=False, description="是否为废标红线条款（level == redline，兼容旧字段）")
    level: str = Field(default="normal", description="redline 不满足即无效投标 / important 不满足按评分扣分 / normal")
    section: str = Field(default="", description="条款在招标文件中的章节路径")
    response_source: str = Field(default="", description="ai 大模型拟稿（待人工核实）/ manual 人工填写")
    response_status: str = Field(default="完全满足", description="响应结果：完全满足/正偏离/负偏离")
    response_detail: str = Field(default="", description="点对点具体实现技术论述与佐证")
    source_fingerprint: str = Field(default="", description="条款来源指纹（确定性抽取溯源）")


class ComplianceCheckReport(BaseModel):
    """标书废标项与合规体检报告（LLM 多轮证据核查制）"""
    passed: bool = Field(..., description="是否通过合规检查")
    total_star_items: int = Field(default=0, description="★号废标条款总数")
    satisfied_star_items: int = Field(default=0, description="已满足★号条款数")
    risk_items: List[Dict[str, str]] = Field(default_factory=list, description="高危/未满足/负偏离风险项（含证据定位与整改建议）")
    warnings: List[str] = Field(default_factory=list, description="建议优化的轻度风险提醒")
    summary: str = Field(default="", description="合规审查总体结论")
    mode: str = Field(default="rules", description="检查模式：llm=多轮证据核查 / rules=关键词快扫")
    checked_sections: int = Field(default=0, description="实际纳入核查的章节数")


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
    stage: str = Field(default="created", description="向导阶段：created/tender_analyzed/outline_confirmed/writing")
    created_at: str
    updated_at: str


class KnowledgeQueryRequest(BaseModel):
    query: str = Field(..., description="检索问题或章节需求")
    top_k: int = Field(default=5, description="召回数量")
    rerank: bool = Field(default=True, description="是否启用 LLM 重排")
    doc_filter: Optional[List[str]] = Field(default=None, description="限定检索的文档 id 列表")


class GenerateSectionRequest(BaseModel):
    project_id: str
    section_id: str
    section_title: str
    section_path: str = ""
    requirements: List[str] = Field(default_factory=list)
    custom_instruction: Optional[str] = Field(default="", description="人工补充的特殊要求或提示词")
    # 引用人工在环：锁定/排除知识块
    pinned_refs: List[str] = Field(default_factory=list)
    excluded_refs: List[str] = Field(default_factory=list)


class GenerateSectionResponse(BaseModel):
    section_id: str
    generated_content: str
    reference_sources: List[Dict[str, Any]] = Field(default_factory=list)
    retrieval_message: str = Field(default="", description="检索状态说明（无匹配/未重排等）")
    mode: str = Field(default="llm", description="生成模式：llm=真实模型 / mock=离线演示")
    tokens_used: int = 0


class UpdateSectionRequest(BaseModel):
    section_id: str
    content: str
    status: Optional[str] = Field(default=None, description="目标状态 pending/completed/reviewed；未传时按正文是否为空取 completed/pending")


class OutlineUpdateRequest(BaseModel):
    """人工编辑大纲（增删改节点、设置要求与字数预算）"""
    outline: List[OutlineNode]


# ==================== 企业集中中台资产模型 (借鉴 Yibiao-Web) ====================

# 企业资料状态：example 预设示例（只展示格式，不进入撰写）/ unverified 待核实 / confirmed 用户确认
AssetStatus = Literal["example", "unverified", "confirmed"]
ASSET_STATUS_FIELD = dict(default="confirmed", description="资料状态：example 预设示例 / unverified 待核实 / confirmed 用户确认")


class CompanyQualification(BaseModel):
    """企业法定与行业资质资产（如涉密、CMMI、ISO、高新等）"""
    id: str
    name: str = Field(..., description="资质证书全称")
    cert_no: str = Field(..., description="证书编号")
    category: str = Field(default="综合资质", description="资质分类: 研发能力/安全保密/服务运维/质量体系/综合资质")
    level: str = Field(default="", description="资质等级: 如 5级/甲级/一级/A级")
    issue_org: str = Field(..., description="发证主管机构")
    issue_date: str = Field(default="", description="生效/发证日期")
    expiry_date: str = Field(default="", description="有效期截止日期")
    summary: str = Field(default="", description="资质适用投标场景与得分说明")
    proof_doc: str = Field(default="", description="证明附件文件或索引编号")
    status: AssetStatus = Field(**ASSET_STATUS_FIELD)


class PersonnelAsset(BaseModel):
    """企业核心技术骨干、项目经理与专家证书履历"""
    id: str
    name: str = Field(..., description="人员姓名")
    role: str = Field(..., description="标书拟任岗位: 项目经理/技术总监/架构师/安全专家/实施工程师")
    # 未填写的履历字段留空，不用"8年/大学本科/高级工程师"之类的默认值代替企业事实
    years_of_experience: Optional[int] = Field(default=None, description="相关行业从业年限")
    education: str = Field(default="", description="最高学历与毕业院校专业")
    professional_title: str = Field(default="", description="技术职称与执业资格")
    certificates: List[str] = Field(default_factory=list, description="持证清单: 如 PMP/CISP/软考高项/ITIL等")
    representative_projects: List[str] = Field(default_factory=list, description="曾担任核心负责人的代表性中标业绩")
    intro: str = Field(default="", description="个人专业能力综合述评（直接用于标书人员简历章节）")
    status: AssetStatus = Field(**ASSET_STATUS_FIELD)


class CaseContract(BaseModel):
    """企业历史同类中标业绩与合同案例资产"""
    id: str
    project_name: str = Field(..., description="业绩项目全称")
    client_name: str = Field(..., description="采购客户单位")
    contract_amount: str = Field(..., description="合同金额 (如 1,280.00 万元)")
    sign_date: str = Field(default="", description="合同签约时间")
    contract_category: str = Field(default="政企软件", description="业务领域分类")
    key_deliverables: List[str] = Field(default_factory=list, description="核心交付模块与技术亮点")
    acceptance_status: str = Field(default="", description="履约验收结论")
    summary: str = Field(default="", description="项目背景与成效总结（可直接插入标书业绩章节）")
    status: AssetStatus = Field(**ASSET_STATUS_FIELD)


class SolutionComponent(BaseModel):
    """企业标准方案可复用技术组件块"""
    id: str
    name: str = Field(..., description="方案组件名称")
    category: str = Field(default="总体架构", description="组件分类: 总体架构/数据治理/信创适配/容灾高可用/安全等保/运维交付")
    tags: List[str] = Field(default_factory=list, description="技术标签")
    summary: str = Field(default="", description="方案组件架构设计概述")
    content: str = Field(..., description="经过实战检验的标准技术方案正文（含架构描述与表格）")
    status: AssetStatus = Field(**ASSET_STATUS_FIELD)


# ==================== 八维质检与降AI味模型 (借鉴 YuduBid) ====================

class QualityDimensionScore(BaseModel):
    """单个维度的质检打分与问题反馈"""
    dimension_name: str = Field(..., description="维度名称")
    score: Optional[int] = Field(default=None, description="得分 0~100；None 表示条件不足未检测（不计入综合分）")
    status: str = Field(default="未检测", description="状态: 优秀/良好/需整改/高危/未检测")
    findings: List[str] = Field(default_factory=list, description="发现的优点或问题")
    suggestions: List[str] = Field(default_factory=list, description="整改建议")


class ScoringCoverage(BaseModel):
    """单个评分项的覆盖核查结果"""
    item_id: str
    name: str
    points: Optional[float] = None
    response_type: str = "proposal"
    status: str = Field(..., description="已覆盖 / 要点缺失 / 篇幅不足 / 未撰写 / 未承接")
    coverage: float = Field(default=0.0, description="覆盖度 0~1")
    section_titles: List[str] = Field(default_factory=list, description="承接章节")
    missing_points: List[str] = Field(default_factory=list, description="未在正文中出现的评分要点")
    word_count: int = 0
    word_budget: Optional[int] = None


class EightDimensionQualityReport(BaseModel):
    """标书八维全盘质量体检报告"""
    overall_score: int = Field(default=0, description="已检测维度的平均分 0~100")
    passed: bool = Field(default=False, description="是否满足投标推荐标准")
    rating_level: str = Field(default="未撰写", description="评级: 甲级推荐/乙级可投/丙级需整改/高危废标风险/未撰写")
    scoring_coverage: List[ScoringCoverage] = Field(default_factory=list, description="评分点逐项覆盖核查")
    scoring_coverage_rate: Optional[float] = Field(default=None, description="按分值加权的评分点覆盖率 0~1")
    dimensions: List[QualityDimensionScore] = Field(default_factory=list, description="八维细分评分与分析")
    de_ai_detected_phrases: List[str] = Field(default_factory=list, description="检测到的 AI 廉价空话套话")
    high_risk_defects: List[str] = Field(default_factory=list, description="一票否决废标缺陷")
    summary: str = Field(default="", description="质检总评结论")
    mode: str = Field(default="rules", description="检查模式：rules=规则快扫 / llm=模型深度审查")


class PolishSectionRequest(BaseModel):
    """章节降AI味与公文润色请求"""
    project_id: str
    section_id: str
    content: str
    polish_mode: str = Field(default="de_ai", description="润色模式: de_ai(去空话强化参数), official_formal(公文严肃化), expand_specs(补充技术细节)")


class PolishSectionResponse(BaseModel):
    """润色结果响应"""
    section_id: str
    original_content: str
    polished_content: str
    improvements: List[str] = Field(default_factory=list, description="所做的改进清单")
    mode: str = Field(default="llm", description="生成模式：llm=真实模型 / mock=离线演示")


# ==================== 项目列表模型 ====================

class ProjectListItem(BaseModel):
    id: str
    name: str
    client_name: str
    description: str
    completion_rate: float
    section_count: int
    completed_sections: int
    stage: str = "created"
    created_at: str
    updated_at: str
