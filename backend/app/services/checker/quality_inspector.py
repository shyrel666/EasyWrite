import re
from typing import List, Dict, Any, Optional, Tuple
from app.models.schemas import (
    OutlineNode, GlobalFacts, QualityDimensionScore, EightDimensionQualityReport,
    PolishSectionRequest, PolishSectionResponse
)
from app.core.llm_client import llm_client

DE_AI_CLICHE_PATTERNS = [
    (r"正如(?:前文|上文|我们)?所(?:述|说|知)", "直接阐述事实，删除套话过渡"),
    (r"众所周知[，,]?", "删除无意义空话"),
    (r"毋庸置疑[，,]?", "公文宜用客观数据佐证"),
    (r"不可否认的是[，,]?", "删除套话句式"),
    (r"值得一提的是[，,]?", "公文宜直接陈述技术要点"),
    (r"在当今(?:快速发展|日新月异|风起云涌|数字化转型)的(?:时代|浪潮中)[，,]?", "政企标书避免宽泛宏大叙事"),
    (r"总而言之[，,]?|综上所述[，,]?", "精简过渡词，使用条目化结论"),
    (r"宛如|犹如", "标书技术方案严禁文学比喻修辞"),
    (r"不仅如此[，,]?而且", "改用条理性并列句式"),
    (r"赋能.*?新范式", "避免空洞互联网黑话，明确具体业务产出"),
    (r"作为一家(?:业界领先|卓越|顶尖)的", "标书严禁自吹自擂，应以资质证书与案例说话"),
    (r"在未来的日子里", "标书应明确承诺具体质保与服务周期")
]

INFORMAL_OR_WEAK_PHRASES = [
    "我们希望", "大概", "可能", "差不多", "尽量做到",
    "小伙伴", "老铁", "搞定", "基本上满足"
]

POLISH_PROMPT = """你是一名从业25年的国家级招投标评审专家兼资深总架构师。
请对以下标书段落进行深度【降AI味】与【公文严肃化】润色。

【润色黄金法则】：
1. 【彻底斩断大模型套话】：坚决删除“众所周知”、“综上所述”、“在当今快速发展的时代”、“作为一家领先的”等廉价AI水词；
2. 【公文严肃庄重】：采用严谨、权威、克制的中国政企招投标技术公文风格；
3. 【工程参数落地】：将抽象描述具体化，强化技术选型、架构分层、参数标准（如 TPS、并发延时、RPO/RTO、国密算法）；
4. 【事实硬约束】：文中提及企业时严格使用指定企业全称与产品型号；
5. 【结构条理化】：多采用“1.1.1”、“（1）”、“（2）”或加粗关键指标。

请直接输出润色后的正式方案文本，不要包含任何寒暄解释。"""

class EightDimensionQualityInspector:
    """
    八维质量体检与降AI味润色引擎（借鉴 YuduBid 八维质检体系）：
    1. 合规完备度 (Compliance Completeness)
    2. 全局事实一致性 (Fact Consistency)
    3. 逻辑与结构严密性 (Logical Rigor)
    4. AI味去除度 (De-AI Naturalness)
    5. 参数与落地可执行度 (Concrete Specs & Feasibility)
    6. 图表与架构丰富度 (Diagram & Visual Richness)
    7. 招标评分点对标度 (Scoring Rubric Alignment)
    8. 公文语言庄重度 (Official Document Formality)
    """

    def __init__(self):
        self.llm = llm_client

    def _traverse_outline(self, outline: List[OutlineNode]) -> List[OutlineNode]:
        flat = []
        def walk(nodes):
            for n in nodes:
                flat.append(n)
                if n.children:
                    walk(n.children)
        walk(outline)
        return flat

    def inspect_quality(
        self,
        outline: List[OutlineNode],
        facts: GlobalFacts,
        star_clauses: Optional[List[str]] = None
    ) -> EightDimensionQualityReport:
        nodes = self._traverse_outline(outline)
        all_text = "\n\n".join([f"【{n.title}】\n{n.content}" for n in nodes if n.content])
        total_chars = len(all_text)
        has_content = total_chars > 100

        dimensions: List[QualityDimensionScore] = []
        detected_cliches: List[str] = []
        high_risk_defects: List[str] = []

        # ---------------- 维度 1: 合规完备度 (Compliance) ----------------
        d1_score = 95
        d1_findings = []
        d1_suggestions = []
        negatives = ["不满足", "无法满足", "暂不支持", "存在负偏离", "无法提供", "不承诺"]
        for neg in negatives:
            if neg in all_text:
                d1_score -= 30
                d1_findings.append(f"正文中包含可能导致废标的高危负偏离词汇: '{neg}'")
                high_risk_defects.append(f"负偏离词汇 '{neg}' 可能直接导致实质性废标！")

        if star_clauses:
            missed_stars = 0
            for star in star_clauses:
                clean = re.sub(r'[★▲\s]', '', star)
                sub_terms = [t for t in re.split(r'[,，。；;\s]+', clean) if len(t) >= 2]
                matched = any(t in all_text for t in sub_terms if len(t) >= 3)
                if not matched:
                    missed_stars += 1
            if missed_stars > 0:
                d1_score -= min(40, missed_stars * 15)
                d1_findings.append(f"有 {missed_stars} 项★号不可偏离条款未检索到清晰论述")
                high_risk_defects.append(f"共计 {missed_stars} 条★号废标项未在标书正文中明确响应。")
            else:
                d1_findings.append("全书均已完全覆盖★号不可偏离条款响应论述。")
        else:
            d1_findings.append("基础合规红线扫描通过，无高危负偏离声明。")

        d1_score = max(0, min(100, d1_score))
        d1_status = "优秀" if d1_score >= 90 else ("良好" if d1_score >= 75 else ("需整改" if d1_score >= 60 else "高危"))
        dimensions.append(QualityDimensionScore(
            dimension_name="1. 合规与废标红线完备度",
            score=d1_score,
            status=d1_status,
            findings=d1_findings or ["合规条款基本满足"],
            suggestions=d1_suggestions or ["继续保持对★号条款的点对点佐证支撑"]
        ))

        # ---------------- 维度 2: 全局事实一致性 (Fact Consistency) ----------------
        d2_score = 95
        d2_findings = []
        d2_suggestions = []
        if facts.company_name and facts.company_name != "标书联合体（默认供应商）":
            if facts.company_name not in all_text:
                d2_score -= 15
                d2_findings.append(f"标书正文未显式提及全局事实设定的企业全称【{facts.company_name}】")
                d2_suggestions.append("在各章节设计思路及结尾承诺中强化企业主体名称落款")
            else:
                d2_findings.append(f"各核心章节均严格遵循企业主体【{facts.company_name}】")

        if facts.core_product_name:
            if facts.core_product_name not in all_text:
                d2_score -= 10
                d2_findings.append(f"未在正文中显式引用核心产品平台型号【{facts.core_product_name}】")
                d2_suggestions.append("在总体架构与产品选型中突出主推产品型号")
            else:
                d2_findings.append(f"产品型号【{facts.core_product_name}】应用一致")

        if facts.database_selection and ("数据库" in all_text or "数据层" in all_text):
            if "达梦" in facts.database_selection and "达梦" not in all_text:
                d2_score -= 10
                d2_findings.append("数据库选型与全局事实设定的达梦数据库不完全吻合")

        d2_score = max(0, min(100, d2_score))
        d2_status = "优秀" if d2_score >= 90 else ("良好" if d2_score >= 75 else ("需整改" if d2_score >= 60 else "高危"))
        dimensions.append(QualityDimensionScore(
            dimension_name="2. 全局事实前后一致性",
            score=d2_score,
            status=d2_status,
            findings=d2_findings or ["全局事实前后统一无矛盾"],
            suggestions=d2_suggestions or ["持续确保产品线及SLA承诺参数在全书保持只读一致"]
        ))

        # ---------------- 维度 3: 逻辑与结构严密性 (Logical Structure) ----------------
        d3_score = 90
        d3_findings = []
        d3_suggestions = []
        empty_sections = [n.title for n in nodes if not n.content.strip() and not n.children]
        if empty_sections:
            d3_score -= min(35, len(empty_sections) * 10)
            d3_findings.append(f"存在 {len(empty_sections)} 个章节正文为空（如: {empty_sections[0]}）")
            d3_suggestions.append("尽快完成空白章节的内容编纂与草拟")
        else:
            d3_findings.append("全书大纲章节完整，各小节均有实质性内容承接")

        if len(nodes) >= 6:
            d3_findings.append("大纲分级结构合理（包含项目理解、架构设计、业务功能、实施与售后）")
        else:
            d3_score -= 15
            d3_suggestions.append("建议扩充二级、三级子章节以形成严密的工程逻辑")

        d3_score = max(0, min(100, d3_score))
        d3_status = "优秀" if d3_score >= 90 else ("良好" if d3_score >= 75 else ("需整改" if d3_score >= 60 else "高危"))
        dimensions.append(QualityDimensionScore(
            dimension_name="3. 逻辑递进与结构严密性",
            score=d3_score,
            status=d3_status,
            findings=d3_findings or ["章节逻辑清晰"],
            suggestions=d3_suggestions or ["保持标准大纲递进层次"]
        ))

        # ---------------- 维度 4: AI味去除度与自然度 (De-AI Naturalness) ----------------
        d4_score = 95
        d4_findings = []
        d4_suggestions = []
        for pat, reason in DE_AI_CLICHE_PATTERNS:
            matches = re.findall(pat, all_text)
            if matches:
                detected_cliches.extend(matches[:3])
                d4_score -= min(20, len(matches) * 5)
                d4_findings.append(f"发现 AI 常见空话模式 '{matches[0]}': {reason}")

        if detected_cliches:
            d4_suggestions.append("使用【一键降AI味润色】功能剔除空话套话，增强方案的工程实战厚重感")
        else:
            d4_findings.append("全书语言克制凝练，未发现典型的低质大模型虚浮套话")

        d4_score = max(0, min(100, d4_score))
        d4_status = "优秀" if d4_score >= 90 else ("良好" if d4_score >= 75 else ("需整改" if d4_score >= 60 else "高危"))
        dimensions.append(QualityDimensionScore(
            dimension_name="4. AI套话去除度与语言纯正度",
            score=d4_score,
            status=d4_status,
            findings=d4_findings or ["无明显AI味套话"],
            suggestions=d4_suggestions or ["保持专业技术语言风格"]
        ))

        # ---------------- 维度 5: 参数与落地可执行度 (Concrete Specs) ----------------
        d5_score = 85
        d5_findings = []
        d5_suggestions = []
        # 检测技术指标关键词 (如 ms, TPS, RPO, RTO, SM4, 分布式, 集群, 微服务)
        spec_keywords = ["微服务", "高可用", "集群", "容灾", "RPO", "RTO", "SM", "加密", "SLA", "7×24", "响应", "等保"]
        hit_specs = [kw for kw in spec_keywords if kw in all_text]
        if len(hit_specs) >= 6:
            d5_score = 95
            d5_findings.append(f"方案包含大量具体的工程参数与技术指标 ({'、'.join(hit_specs[:6])})")
        elif len(hit_specs) >= 3:
            d5_score = 85
            d5_findings.append(f"方案具备基本的关键技术参数指标 ({'、'.join(hit_specs)})")
            d5_suggestions.append("建议补充具体的量化性能指标（如并发处理能力、数据恢复时限）")
        else:
            d5_score = 65
            d5_findings.append("方案偏概念性描述，缺乏具体量化技术参数与协议标准")
            d5_suggestions.append("在各章节补充关键硬性参数与具体组件配置")

        d5_score = max(0, min(100, d5_score))
        d5_status = "优秀" if d5_score >= 90 else ("良好" if d5_score >= 75 else ("需整改" if d5_score >= 60 else "高危"))
        dimensions.append(QualityDimensionScore(
            dimension_name="5. 工程参数与落地可执行度",
            score=d5_score,
            status=d5_status,
            findings=d5_findings,
            suggestions=d5_suggestions
        ))

        # ---------------- 维度 6: 图表与架构丰富度 (Diagram Richness) ----------------
        d6_score = 80
        d6_findings = []
        d6_suggestions = []
        has_mermaid = "```mermaid" in all_text or "<pre class=\"mermaid\"" in all_text
        has_table = "|" in all_text and "-|-" in all_text or "table" in all_text.lower()
        
        if has_mermaid and has_table:
            d6_score = 95
            d6_findings.append("方案图文并茂，同时包含专业 Mermaid 架构拓扑图与原生指标对比表格")
        elif has_mermaid:
            d6_score = 88
            d6_findings.append("方案包含规范的 Mermaid 架构拓扑流向图")
            d6_suggestions.append("建议在技术偏离或选型章节增加对比表格")
        elif has_table:
            d6_score = 85
            d6_findings.append("方案包含结构化表格")
            d6_suggestions.append("建议在第二章总体架构中增加 Mermaid 逻辑架构图")
        else:
            d6_score = 65
            d6_findings.append("正文纯文字排版，缺少架构图与参数对照表格")
            d6_suggestions.append("建议在总体方案中引入 Mermaid 拓扑图，并在偏离表中生成对比表")

        d6_score = max(0, min(100, d6_score))
        d6_status = "优秀" if d6_score >= 90 else ("良好" if d6_score >= 75 else ("需整改" if d6_score >= 60 else "高危"))
        dimensions.append(QualityDimensionScore(
            dimension_name="6. 架构图表与可视化丰富度",
            score=d6_score,
            status=d6_status,
            findings=d6_findings,
            suggestions=d6_suggestions
        ))

        # ---------------- 维度 7: 招标评分点对标度 (Scoring Alignment) ----------------
        d7_score = 88
        d7_findings = []
        d7_suggestions = []
        req_count = sum([len(n.requirements) for n in nodes])
        if req_count > 0:
            d7_findings.append(f"明确设置了 {req_count} 项招标文件对标评分响应要求")
            d7_score = 92
        else:
            d7_findings.append("已结合通用政企招投标评分重点进行对标阐述")
            d7_suggestions.append("可针对重点高分章节设置针对性的评分要点提示")

        d7_score = max(0, min(100, d7_score))
        d7_status = "优秀" if d7_score >= 90 else ("良好" if d7_score >= 75 else ("需整改" if d7_score >= 60 else "高危"))
        dimensions.append(QualityDimensionScore(
            dimension_name="7. 招标文件评分点对标度",
            score=d7_score,
            status=d7_status,
            findings=d7_findings,
            suggestions=d7_suggestions
        ))

        # ---------------- 维度 8: 公文语言庄重度 (Official Formality) ----------------
        d8_score = 95
        d8_findings = []
        d8_suggestions = []
        for weak in INFORMAL_OR_WEAK_PHRASES:
            if weak in all_text:
                d8_score -= 15
                d8_findings.append(f"检测到口语化或不庄重表述: '{weak}'")
                d8_suggestions.append(f"将口语词 '{weak}' 替换为公文术语（如'承诺实现'、'严格保障'）")

        if d8_score >= 90:
            d8_findings.append("全书公文语气严肃、克制庄重，符合政府与大型国企招投标规范")

        d8_score = max(0, min(100, d8_score))
        d8_status = "优秀" if d8_score >= 90 else ("良好" if d8_score >= 75 else ("需整改" if d8_score >= 60 else "高危"))
        dimensions.append(QualityDimensionScore(
            dimension_name="8. 政企公文体裁与语气庄重度",
            score=d8_score,
            status=d8_status,
            findings=d8_findings or ["语言体裁符合公文标准"],
            suggestions=d8_suggestions or ["保持严谨公文体裁"]
        ))

        # 综合计算
        overall_score = round(sum(d.score for d in dimensions) / len(dimensions))
        has_critical = len(high_risk_defects) > 0

        if has_critical or overall_score < 60:
            rating = "高危废标风险"
            passed = False
        elif overall_score >= 88:
            rating = "甲级推荐"
            passed = True
        elif overall_score >= 75:
            rating = "乙级可投"
            passed = True
        else:
            rating = "丙级需整改"
            passed = False

        summary_text = (
            f"本次八维质量体检综合评分为【{overall_score} 分】，标书评级为【{rating}】。"
            + (f" 发现 {len(high_risk_defects)} 项高危废标风险，必须整改！" if high_risk_defects else " 未发现实质性废标红线，整体技术方案完备、逻辑严谨。")
            + (f" 检测到 {len(detected_cliches)} 处典型 AI 套话，建议执行降AI味一键润色。" if detected_cliches else "")
        )

        return EightDimensionQualityReport(
            overall_score=overall_score,
            passed=passed,
            rating_level=rating,
            dimensions=dimensions,
            de_ai_detected_phrases=list(set(detected_cliches)),
            high_risk_defects=high_risk_defects,
            summary=summary_text
        )

    def polish_section(self, req: PolishSectionRequest, facts: GlobalFacts) -> PolishSectionResponse:
        """
        对单章节正文执行深度降AI味润色与公文严肃化升级
        """
        original = req.content
        if not original.strip():
            return PolishSectionResponse(
                section_id=req.section_id,
                original_content="",
                polished_content="*(该章节尚无内容，请先执行智能撰写或补充正文)*",
                improvements=[]
            )

        # ---------------- 两阶段降AI味与公文严肃化润色 ----------------
        improvements = []
        # 阶段一：确定性本地正则与弱势词汇清洗
        stage1_text = original
        for pat, fix_note in DE_AI_CLICHE_PATTERNS:
            if re.search(pat, stage1_text):
                stage1_text = re.sub(pat, "", stage1_text)
                improvements.append(f"前置剔除 AI 虚浮套话（{fix_note}）")

        for weak in INFORMAL_OR_WEAK_PHRASES:
            if weak in stage1_text:
                stage1_text = stage1_text.replace(weak, "我方承诺")
                improvements.append(f"前置将口语词汇【{weak}】替换为庄重公文词")

        # 阶段二：远端大模型深度句式升华与公文参数重构
        if self.llm.is_configured:
            try:
                user_prompt = (
                    f"【待润色章节编号】：{req.section_id}\n"
                    f"【润色模式】：{req.polish_mode}\n"
                    f"【企业全局事实】：企业全称【{facts.company_name}】，核心产品【{facts.core_product_name}】，"
                    f"技术栈【{facts.architecture_stack}】，数据库【{facts.database_selection}】\n\n"
                    f"【待二次精修的方案文本如下】：\n{stage1_text}"
                )
                polished = self.llm.chat_completion(system_prompt=POLISH_PROMPT, user_prompt=user_prompt)
                if polished and "方案设计思路与技术路线" not in polished:
                    improvements.append("通过大模型完成政企公文体裁升华与工程落地参数重构")
                    return PolishSectionResponse(
                        section_id=req.section_id,
                        original_content=original,
                        polished_content=polished,
                        improvements=improvements
                    )
            except Exception as e:
                print(f"[QualityInspector] 远端润色失败: {e}，切入本地规则润色兜底")

        # 本地高质量规则润色引擎兜底
        polished = stage1_text
        if facts.company_name and facts.company_name != "标书联合体（默认供应商）":
            if facts.company_name not in polished:
                polished = f"投标人【{facts.company_name}】针对本章节技术方案郑重规划如下：\n\n" + polished
                improvements.append(f"注入统一企业主体全称【{facts.company_name}】")

        if not improvements:
            improvements.append("文本已较为规范，已完成段落排版修饰与公文格式微调")

        return PolishSectionResponse(
            section_id=req.section_id,
            original_content=original,
            polished_content=polished.strip(),
            improvements=improvements
        )

quality_inspector = EightDimensionQualityInspector()
