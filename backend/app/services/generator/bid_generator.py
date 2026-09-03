from typing import List, Dict, Any, Optional, Tuple
from app.core.llm_client import llm_client
from app.services.rag.vector_store import knowledge_store
from app.services.assets.asset_manager import asset_manager
from app.models.schemas import KnowledgeChunk, OutlineNode, GlobalFacts

SYSTEM_BID_WRITER_PROMPT = """你是一名拥有15年政企信息化与软件工程经验的资深技术标书总架构师。
你的职责是编写极其严谨、专业、详实且完全响应招标文件要求的高分技术投标方案。

【核心原则与红线要求】：
1. 【全局事实不可违背】：必须严格遵守用户设定的【全局事实约束】（投标企业全称、统一代码、核心产品平台名称、指定数据库底座、SLA标准），绝对禁止擅自篡改、杜撰冲突型号或产生前后矛盾。
2. 【杜绝空话套话】：严禁车轱辘话，方案必须包含具体的架构分层、模块划分、技术选型、设计模式或行业标准。
3. 【点对点响应】：严格点对点响应招标要求，特别是带有“★”号的关键条款，必须在正文中给出清晰、明确的承接论述。
4. 【图文结合】：如果本节涉及系统架构、技术路线、业务流程或数据流向，请用标准 ```mermaid 语法绘制一张高清晰度架构图或流转图，以便渲染插入标书。
5. 【公文排版规范】：结构清晰，逻辑严密，多采用“1.1.1”、“1.1.2”或“（1）”、“（2）”的条目化论述。
"""

OUTLINE_GEN_PROMPT = """你是一名从业20年的国家级招投标规划专家兼资深总架构师。
请根据以下招标项目的背景需求与技术评分要求，为该投标项目量身定制一套符合《政府采购和招标投标管理办法》、评分点全覆盖的技术标书结构化大纲树。

【设计原则】：
1. 涵盖项目理解、总体技术路线（含微服务/信创/高可用）、各业务子系统详细设计、项目实施团队管理、售后培训SLA等核心篇章。
2. 必须在技术架构章节明确包含 Mermaid 总体拓扑图要求。
3. 请以严格的单个 JSON 对象输出，不得包含额外寒暄。

【输出 JSON 模式】：
{
  "outline": [
    {
      "id": "sec_1",
      "title": "第一章 项目理解与建设目标",
      "level": 1,
      "requirements": ["深刻理解业务痛点与技术现状"],
      "children": [
        { "id": "sec_1_1", "title": "1.1 项目建设背景与业务现状分析", "level": 2, "requirements": [] },
        { "id": "sec_1_2", "title": "1.2 建设原则与总体建设目标", "level": 2, "requirements": [] }
      ]
    },
    {
      "id": "sec_2",
      "title": "第二章 总体技术架构与方案设计",
      "level": 1,
      "requirements": ["微服务技术路线、信创国产化适配、Mermaid总体架构拓扑图、数据安全等保"],
      "children": [
        { "id": "sec_2_1", "title": "2.1 总体逻辑架构设计与拓扑流转", "level": 2, "requirements": [] },
        { "id": "sec_2_2", "title": "2.2 核心微服务选型与高并发保障", "level": 2, "requirements": [] },
        { "id": "sec_2_3", "title": "2.3 信创环境适配与达梦数据库集成", "level": 2, "requirements": [] },
        { "id": "sec_2_4", "title": "2.4 系统高可用容灾与等保三级设计", "level": 2, "requirements": [] }
      ]
    }
  ]
}
"""

CHAPTER_SPECIALIZED_PROMPTS = {
    "arch": """你是一名资深云原生与信创分布式架构师。请针对架构设计章节，侧重微服务治理、容器弹性调度、高可用双活容灾、国产信创适配（统信/麒麟/达梦）、数据加密等指标，必须在第二小节输出规范的 ```mermaid 架构拓扑图。语言必须严密、权威、杜绝AI空话。""",
    "team": """你是一名资深国家注册 PMP 高级项目经理与人社部高级工程师。请针对团队配置章节，侧重项目经理执业资质、核心团队驻场保障、知识转移与人员考核制度，以专业规范的表格和严谨公文体裁论述。""",
    "maintenance": """你是一名资深 ITIL/ITSS 运维保障专家。请针对售后运维保障章节，明确承诺 7×24 小时极速响应、现场驻场工程师排期、例行安全巡检频率、重大活动保活保障方案及量化 SLA 指标。"""
}

class BidGenerator:
    """
    标书智能生成引擎（深度融合真实大模型驱动、全局事实硬约束与中台资产）：
    1. 大模型个性化 WBS 大纲提炼（根据实际项目要求）
    2. 基于大模型的高质量分章撰写与 SSE 流式输出
    3. 专业细分领域 System Prompt 自动装配
    4. 离线/无 Key 时高保真安全降级
    """

    def __init__(self):
        self.llm = llm_client
        self.kb = knowledge_store

    def generate_outline_from_rfp(self, rfp_content_summary: str) -> List[OutlineNode]:
        """
        根据招标文件摘要/要求，调用大模型智能生成高度对标的定制技术标大纲树
        """
        if self.llm.is_configured:
            try:
                data = self.llm.chat_completion_structured(
                    system_prompt=OUTLINE_GEN_PROMPT,
                    user_prompt=f"【待投标项目招标需求概要】：\n{rfp_content_summary}"
                )
                if data and "outline" in data and isinstance(data["outline"], list):
                    nodes = []
                    for c_idx, ch in enumerate(data["outline"], 1):
                        ch_id = ch.get("id") or f"sec_{c_idx}"
                        ch_title = ch.get("title") or f"第{c_idx}章"
                        ch_reqs = ch.get("requirements", [])
                        
                        children_nodes = []
                        for sub_idx, sub in enumerate(ch.get("children", []), 1):
                            sub_id = sub.get("id") or f"{ch_id}_{sub_idx}"
                            sub_title = sub.get("title") or f"{c_idx}.{sub_idx}"
                            children_nodes.append(OutlineNode(
                                id=sub_id,
                                title=sub_title,
                                level=2,
                                path=f"{ch_title} > {sub_title}",
                                requirements=sub.get("requirements", [])
                            ))
                        
                        nodes.append(OutlineNode(
                            id=ch_id,
                            title=ch_title,
                            level=1,
                            path=ch_title,
                            requirements=ch_reqs,
                            children=children_nodes
                        ))
                    if nodes:
                        return nodes
            except Exception as e:
                print(f"[BidGenerator] AI 大纲定制生成失败: {e}，切入内置标准大纲")

        # 内置标准化软件技术标大纲兜底
        default_outline = [
            OutlineNode(
                id="sec_1",
                title="第一章 项目理解与建设目标",
                level=1,
                path="第一章 项目理解与建设目标",
                requirements=["理解业务痛点与建设愿景"],
                children=[
                    OutlineNode(id="sec_1_1", title="1.1 项目建设背景与现状分析", level=2, path="第一章 项目理解与建设目标 > 1.1 项目建设背景与现状分析"),
                    OutlineNode(id="sec_1_2", title="1.2 建设原则与总体目标", level=2, path="第一章 项目理解与建设目标 > 1.2 建设原则与总体目标"),
                ]
            ),
            OutlineNode(
                id="sec_2",
                title="第二章 总体技术架构与方案设计",
                level=1,
                path="第二章 总体技术架构与方案设计",
                requirements=["总体逻辑架构、微服务路线、数据与安全架构、包含Mermaid架构拓扑图"],
                children=[
                    OutlineNode(id="sec_2_1", title="2.1 系统总体逻辑架构设计", level=2, path="第二章 总体技术架构与方案设计 > 2.1 系统总体逻辑架构设计"),
                    OutlineNode(id="sec_2_2", title="2.2 技术路线与微服务选型", level=2, path="第二章 总体技术架构与方案设计 > 2.2 技术路线与微服务选型"),
                    OutlineNode(id="sec_2_3", title="2.3 系统高可用与容灾备份方案", level=2, path="第二章 总体技术架构与方案设计 > 2.3 系统高可用与容灾备份方案"),
                    OutlineNode(id="sec_2_4", title="2.4 信息安全与等级保护设计", level=2, path="第二章 总体技术架构与方案设计 > 2.4 信息安全与等级保护设计"),
                ]
            ),
            OutlineNode(
                id="sec_3",
                title="第三章 详细功能与业务实现方案",
                level=1,
                path="第三章 详细功能与业务实现方案",
                requirements=["对照招标功能清单逐项阐述"],
                children=[
                    OutlineNode(id="sec_3_1", title="3.1 核心业务子系统方案设计", level=2, path="第三章 详细功能与业务实现方案 > 3.1 核心业务子系统方案设计"),
                    OutlineNode(id="sec_3_2", title="3.2 数据接入与共享交换中台", level=2, path="第三章 详细功能与业务实现方案 > 3.2 数据接入与共享交换中台"),
                ]
            ),
            OutlineNode(
                id="sec_4",
                title="第四章 项目实施与交付进度管理",
                level=1,
                path="第四章 项目实施与交付进度管理",
                requirements=["实施组织架构、里程碑工期、质量保证"],
                children=[
                    OutlineNode(id="sec_4_1", title="4.1 实施团队组织架构与人员配置", level=2, path="第四章 项目实施与交付进度管理 > 4.1 实施团队组织架构与人员配置"),
                    OutlineNode(id="sec_4_2", title="4.2 项目实施进度计划与里程碑", level=2, path="第四章 项目实施与交付进度管理 > 4.2 项目实施进度计划与里程碑"),
                ]
            ),
            OutlineNode(
                id="sec_5",
                title="第五章 售后服务保障与培训体系",
                level=1,
                path="第五章 售后服务保障与培训体系",
                requirements=["SLA响应时间、巡检机制、培训计划"],
                children=[
                    OutlineNode(id="sec_5_1", title="5.1 售后运维支持与SLA承诺", level=2, path="第五章 售后服务保障与培训体系 > 5.1 售后运维支持与SLA承诺"),
                    OutlineNode(id="sec_5_2", title="5.2 知识转移与用户培训方案", level=2, path="第五章 售后服务保障与培训体系 > 5.2 知识转移与用户培训方案"),
                ]
            )
        ]
        return default_outline

    def draft_section(
        self,
        section_title: str,
        section_path: str,
        requirements: List[str],
        custom_instruction: str = "",
        facts: Optional[GlobalFacts] = None
    ) -> Dict[str, Any]:
        """
        执行单章节的核心撰写流水线：
        1. 知识库定向召回（结合章节标题与具体要求）
        2. 装配包含【全局事实约束】与【Mermaid 绘图要求】的专属 Prompt
        3. 调用大模型生成高质量草稿
        4. 附带引用溯源资产
        """
    def _select_system_prompt(self, section_title: str) -> str:
        if any(kw in section_title for kw in ["架构", "拓扑", "技术路线", "信创", "高可用"]):
            return CHAPTER_SPECIALIZED_PROMPTS["arch"]
        elif any(kw in section_title for kw in ["团队", "人员", "组织架构", "资质", "配置"]):
            return CHAPTER_SPECIALIZED_PROMPTS["team"]
        elif any(kw in section_title for kw in ["售后", "运维", "SLA", "巡检", "维保", "培训"]):
            return CHAPTER_SPECIALIZED_PROMPTS["maintenance"]
        return SYSTEM_BID_WRITER_PROMPT

    def _build_prompts(
        self,
        section_title: str,
        section_path: str,
        requirements: List[str],
        custom_instruction: str = "",
        facts: Optional[GlobalFacts] = None
    ) -> Tuple[str, str, List[Dict[str, Any]], Dict[str, Any], GlobalFacts]:
        query_terms = f"{section_title} {' '.join(requirements)} {custom_instruction}"
        matched_chunks = self.kb.search(query=query_terms, top_k=3)
        
        ref_text_blocks = []
        for i, c in enumerate(matched_chunks, start=1):
            ref_text_blocks.append(
                f"[参考资产{i}] 来源文档: {c['doc_name']}\n"
                f"所属大纲路径: {c['breadcrumb']}\n"
                f"历史方案内容:\n{c['content']}\n"
            )
        reference_context = "\n---\n".join(ref_text_blocks) if ref_text_blocks else "（无完全匹配的历史章节，请基于业界顶级规范自主设计）"

        matched_asset = asset_manager.match_assets_for_section(section_title, requirements)
        asset_context = f"\n\n【企业中台权威资产库匹配（优先复用与论述）】：\n{matched_asset['context_text']}" if matched_asset.get("context_text") else ""

        facts_obj = facts or GlobalFacts()
        facts_constraint_text = facts_obj.to_constraint_text()

        user_prompt = f"""【当前待撰写章节】：{section_title}
【完整大纲路径】：{section_path or section_title}

【本项目的全局事实约束（强制遵守，前后统一）】：
{facts_constraint_text}

【本章节须响应的招标要点/评分项】：
{chr(10).join([f"- {req}" for req in requirements]) if requirements else "- 详实阐述该模块的具体设计与方案保障"}

【人工补充指导意见】：
{custom_instruction if custom_instruction else "无特殊补充，按业内顶级政企技术标标准编写"}
{asset_context}

【企业历史中标标书参考资料】：
{reference_context}

【编写任务】：
请针对上述章节，融合参考资料与企业中台资产中的成熟经验与本次招标的具体要求，输出详尽、专业的技术标书正文。
要求：
1. 方案行文中必须体现【{facts_obj.company_name}】与核心产品【{facts_obj.core_product_name}】的具体应用与保障；
2. 如涉及架构设计或流转机制，请附带一段规范的 ```mermaid 架构图；
3. 严格遵守政企标书语言风格，分层、分点阐述；
4. 直接输出正文内容，无需输出寒暄废话。"""

        system_prompt = self._select_system_prompt(section_title)
        return system_prompt, user_prompt, matched_chunks, matched_asset, facts_obj

    def draft_section(
        self,
        section_title: str,
        section_path: str,
        requirements: List[str],
        custom_instruction: str = "",
        facts: Optional[GlobalFacts] = None
    ) -> Dict[str, Any]:
        """
        执行单章节的核心撰写流水线（同步完整返回）
        """
        system_prompt, user_prompt, matched_chunks, matched_asset, facts_obj = self._build_prompts(
            section_title, section_path, requirements, custom_instruction, facts
        )

        if self.llm.is_configured:
            generated_content = self.llm.chat_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt
            )
        else:
            generated_content = self._generate_rich_mock_content(section_title, facts_obj, matched_asset)

        formatted_refs = [
            KnowledgeChunk(
                id=c.get("id", ""),
                doc_name=c.get("doc_name", ""),
                section_title=c.get("section_title", ""),
                breadcrumb=c.get("breadcrumb", ""),
                content=c.get("content", ""),
                tags=c.get("tags", []),
                score=c.get("score", 0.0)
            ) for c in matched_chunks
        ]

        return {
            "section_title": section_title,
            "generated_content": generated_content,
            "references": formatted_refs
        }

    def draft_section_stream(
        self,
        section_title: str,
        section_path: str,
        requirements: List[str],
        custom_instruction: str = "",
        facts: Optional[GlobalFacts] = None
    ):
        """
        执行单章节流式打字机生成（逐步 yield token 字符串）
        """
        system_prompt, user_prompt, _, _, _ = self._build_prompts(
            section_title, section_path, requirements, custom_instruction, facts
        )
        return self.llm.chat_completion_stream(
            system_prompt=system_prompt,
            user_prompt=user_prompt
        )

    def _generate_rich_mock_content(self, section_title: str, facts: GlobalFacts, matched_asset: Optional[Dict[str, Any]] = None) -> str:
        """为测试提供融合全局事实、中台资产与 Mermaid 图表的高质量技术标内容"""
        # 针对人员团队章节生成专业团队表格
        if any(kw in section_title for kw in ["实施团队", "人员配置", "项目团队", "组织架构"]):
            personnel = asset_manager.list_personnel()
            lines = [
                f"### 1. 项目组织管理架构\n"
                f"投标人【{facts.company_name}】高度重视本项目的实施交付，设立由高级管理层挂帅的专项项目部，"
                f"指派具备国家注册资格的高级项目经理与资深系统架构师驻场把关。\n\n"
                f"### 2. 拟任核心技术骨干人员配置表\n\n"
                f"| 拟任岗位 | 姓名 | 学历与院校 | 从业年限 | 执业资格证书与职称 | 代表性中标业绩 |\n"
                f"| :---: | :---: | :---: | :---: | :--- | :--- |"
            ]
            for p in personnel:
                certs = "、".join(p.certificates[:3])
                proj = p.representative_projects[0] if p.representative_projects else "国家级政企重点工程"
                lines.append(f"| {p.role} | **{p.name}** | {p.education} | {p.years_of_experience}年 | {p.professional_title}<br/>({certs}) | {proj} |")
            lines.append(f"\n### 3. 项目人员稳定性与考核机制\n我方承诺：本项目核心骨干在实施与试运行期间保证 100% 专职驻场，未经采购方书面同意绝不擅自更换。")
            return "\n".join(lines)

        # 针对资质合规章节生成资质一览表
        if any(kw in section_title for kw in ["资质", "准入", "企业资信"]):
            quals = asset_manager.list_qualifications()
            lines = [
                f"### 1. 投标人法定资质与行业认证承诺\n"
                f"投标人【{facts.company_name}】（统一代码：{facts.credit_code}）具备完全独立合法的投标资格，已获得多项权威认证：\n\n"
                f"| 序号 | 资质认证名称 | 资质等级 | 证书编号 | 发证机构 | 有效期截止 |\n"
                f"| :---: | :--- | :---: | :---: | :--- | :---: |"
            ]
            for idx, q in enumerate(quals, 1):
                lines.append(f"| {idx} | **{q.name}** | {q.level or '合格'} | {q.cert_no} | {q.issue_org} | {q.expiry_date} |")
            lines.append(f"\n### 2. 合规核查结论\n经核查，我方资质证书全部在有效期内，完全满足并优于招标资质门槛要求，已备齐所有原件备查。")
            return "\n".join(lines)

        # 针对历史业绩章节生成案例表
        if any(kw in section_title for kw in ["业绩", "案例", "项目经历"]):
            cases = asset_manager.list_cases()
            lines = [
                f"### 1. 近三年同类重大成功业绩清单\n"
                f"投标人【{facts.company_name}】在类似信息化工程领域拥有深厚的建设底蕴，以下为近三年代表性标杆案例：\n\n"
                f"| 序号 | 业绩项目全称 | 采购客户单位 | 合同金额 | 签约时间 | 验收与运行成效 |\n"
                f"| :---: | :--- | :--- | :---: | :---: | :--- |"
            ]
            for idx, c in enumerate(cases, 1):
                lines.append(f"| {idx} | **{c.project_name}** | {c.client_name} | {c.contract_amount} | {c.sign_date} | {c.acceptance_status} |")
            lines.append(f"\n### 2. 标杆项目成效佐证\n上述项目合同复印件及第三方终验报告已作为标书附件全量装订，技术成熟可靠。")
            return "\n".join(lines)

        # 默认：技术方案与 Mermaid 架构图
        return (
            f"### 1. 方案设计思路与技术路线\n"
            f"针对本项目建设要求，投标人【{facts.company_name}】依托成熟的企业级产品【{facts.core_product_name}】，"
            f"采用【{facts.architecture_stack}】构建高可用、易扩展的业务平台。系统底层全面适配【{facts.database_selection}】。\n\n"
            f"### 2. 总体架构拓扑图\n"
            f"系统总体架构涵盖接入层、应用网关层、微服务业务层与持久化数据层，其拓扑流转关系如下图所示：\n\n"
            f"```mermaid\n"
            f"graph TD\n"
            f"    User[终端用户 / 业务前台] --> Gateway[API智能网关 / 安全鉴权中心]\n"
            f"    Gateway --> Svc1[核心业务微服务群]\n"
            f"    Gateway --> Svc2[数据治理与共享服务]\n"
            f"    Svc1 --> DB[(主备高可用数据库集群)]\n"
            f"    Svc2 --> DB\n"
            f"```\n\n"
            f"### 3. 点对点技术响应与质量保障\n"
            f"- **合规性承诺**：系统完全符合国家网络安全等级保护三级规范，核心数据全面支持国密（SM2/SM3/SM4）加密传输与存储。\n"
            f"- **交付与SLA支持**：我司郑重承诺：{facts.delivery_guarantee}；在售后阶段提供：{facts.sla_commitment}。"
        )

bid_generator = BidGenerator()
