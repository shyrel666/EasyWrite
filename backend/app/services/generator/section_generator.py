"""
章节撰写引擎（RAG 混合检索 + 跨章连贯上下文 + 事实完备纪律）。

对比旧版 bid_generator 的核心升级：
1. 检索走 retrieval_service 完整管线（多查询→双路召回→RRF→LLM重排→阈值→父级扩展），
   引用全程溯源，支持锁定/排除人工在环；旧版是 TF-IDF 直取 top3、无阈值（垃圾也注入）
2. 跨章连贯：注入父章节与已完成兄弟章节的摘要，防止"前文K8s后文Swarm"式矛盾
3. 事实完备模式：omit/placeholder 两档，未提供的事实不再被模型自由编造
4. 领域专家 System Prompt 按章节主题自动装配（保留）
5. SSE 流式走 AsyncOpenAI，不阻塞事件循环
6. "选资料"与"写正文"分离：依据由 evidence_set.select_evidence 选好后传入，build_prompts 是纯函数
"""
import re
import logging
from typing import Dict, Any, List, Optional, AsyncGenerator, Tuple

from app.core.llm_client import llm_client
from app.models.schemas import OutlineNode, GlobalFacts, Project
from app.services.generator.evidence_set import EvidenceSet

logger = logging.getLogger("easywrite.section")

SYSTEM_BID_WRITER_PROMPT = """你是一名拥有15年政企信息化与软件工程经验的资深技术标书总架构师。
你的职责是编写极其严谨、专业、详实且完全响应招标文件要求的高分技术投标方案。

【核心原则与红线要求】：
1. 【全局事实不可违背】：必须严格遵守【全局事实约束】（投标企业全称、统一代码、核心产品平台、数据库底座、SLA标准），绝对禁止擅自篡改、杜撰冲突型号或产生前后矛盾。
2. 【杜绝空话套话】：严禁车轱辘话与AI味连接词（"综上所述""不难看出""值得一提的是"），方案必须包含具体的架构分层、模块划分、技术选型、设计模式或行业标准编号。
3. 【点对点响应】：严格点对点响应招标要求，特别是带"★"号的关键条款，必须在正文中给出清晰、明确的承接论述。
4. 【图文结合】：如果本节涉及系统架构、技术路线、业务流程或数据流向，请用标准 ```mermaid 语法绘制一张高清晰度架构图或流转图。
5. 【公文排版规范】：结构清晰，逻辑严密，多采用"1.1.1"、"1.1.2"或"（1）"、"（2）"的条目化论述；不要输出本节标题（标题由大纲体系承担），直接从正文开始；正文内的小标题用加粗的"**1.1.1 xxx**"形式，不使用 # 号标题。
6. 【参考资料纪律】：如提供了历史标书参考资料，只吸收其思路并改写融入本项目语境，严禁整段照抄，正文中绝不出现"知识库""参考资料"等来源字样；参考资料中的数值是其他项目的，不能当作本项目的承诺。
7. 【承诺纪律】：响应时限、到场时间、驻场人数、巡检频率、性能指标、工期与质保期等量化承诺，只能取自【全局事实约束】；全局事实未给出时按事实完备纪律处理（【待填写】占位或模糊表述），不得自行给出数值，也不得把招标要求直接改写成我方的具体承诺。
"""

CHAPTER_SPECIALIZED_PROMPTS = {
    "arch": "你是一名资深云原生与信创分布式架构师。请针对架构设计章节，侧重微服务治理、容器弹性调度、高可用与容灾、信创适配、数据加密等设计，必须输出规范的 ```mermaid 架构拓扑图。操作系统、数据库、中间件等选型以全局事实为准，未指定时不替企业选定具体产品；性能与容灾指标不自行给出数值。语言必须严密、权威、杜绝AI空话。",
    "team": "你是一名资深国家注册 PMP 高级项目经理与人社部高级工程师。请针对团队配置章节，侧重岗位设置与职责、项目经理与核心成员的资质要求、驻场保障、知识转移与人员考核制度，以专业规范的表格和严谨公文体裁论述。人员姓名、证书、从业年限与驻场人数只能来自企业资料或全局事实，未提供时按事实完备纪律处理。",
    "maintenance": "你是一名资深 ITIL/ITSS 运维保障专家。请针对售后运维保障章节，侧重服务组织与流程、故障分级与响应机制、驻场与巡检安排、重大活动保障方案。响应时限、驻场人数、巡检频率等量化指标以全局事实为准；全局事实未提供时按事实完备纪律处理（【待填写】占位或模糊表述），不得自行给出数值。",
}


# 智能完善的修订方式（policy.decide 给出）：上一轮没有进展时换方法；连续两轮没有进展时只处理阻塞问题
REVISION_MODE_HINTS = {
    "alternate": "上一轮修订后，这些问题仍未解决。请换一种修改方式：不要只在原句上做局部替换，改为重写涉及问题的段落；"
                 "缺失的评分要点单独成条论述（资料不足处用【待填写】占位）。",
    "minimal": "本轮只处理上面列出的阻塞问题，其余段落（包括篇幅与措辞）一律保持原样，不做其他改动。",
}


def _select_system_prompt(section_title: str) -> str:
    """专项角色提示叠加在通用红线规则之上（不能替换掉全局事实/禁套话/不出标题等规则）"""
    specialized = None
    if any(kw in section_title for kw in ["架构", "拓扑", "技术路线", "信创", "高可用"]):
        specialized = CHAPTER_SPECIALIZED_PROMPTS["arch"]
    elif any(kw in section_title for kw in ["团队", "人员", "组织架构", "资质", "配置"]):
        specialized = CHAPTER_SPECIALIZED_PROMPTS["team"]
    elif any(kw in section_title for kw in ["售后", "运维", "SLA", "巡检", "维保", "培训"]):
        specialized = CHAPTER_SPECIALIZED_PROMPTS["maintenance"]
    return f"{SYSTEM_BID_WRITER_PROMPT}\n【本章专项要求】：{specialized}" if specialized else SYSTEM_BID_WRITER_PROMPT


def _title_core(text: str) -> str:
    text = re.sub(r"[#*\s]", "", text or "")
    return re.sub(r"^(?:第[一二三四五六七八九十百\d]+[章节篇]|[\d.]+|[一二三四五六七八九十]+[、.．])", "", text)


def strip_title_heading(content: str, section_title: str) -> str:
    """模型常把本节标题当首行标题重复输出（导出 Word 会出现两次）：去掉与章节标题相同的开头标题行"""
    body = (content or "").lstrip("\n")
    first, _, rest = body.partition("\n")
    m = re.match(r"^\s*(?:#{1,6}\s*(.+)|\*\*(.+)\*\*)\s*$", first)
    if not m:
        return content
    heading, title = _title_core(m.group(1) or m.group(2)), _title_core(section_title)
    if heading and title and (heading == title or title in heading):
        return rest.lstrip("\n")
    return content


def _sibling_context(outline: List[OutlineNode], section_id: str, max_siblings: int = 3) -> str:
    """
    跨章连贯上下文：定位目标节点，取父链标题 + 已完成兄弟章节的内容摘要。
    防止各章节独立生成导致的技术路线/口径矛盾（全局事实之外的另一层一致性保障）。
    """
    found = []

    def walk(nodes, ancestors):
        for n in nodes:
            if n.id == section_id:
                found.append((n, ancestors, nodes))
                return True
            if walk(n.children, ancestors + [n]):
                return True
        return False

    walk(outline, [])
    if not found:
        return ""
    node, ancestors, siblings = found[0]

    parts = []
    if ancestors:
        parts.append("本章上级章节脉络：" + " > ".join(a.title for a in ancestors))
    done = [s for s in siblings if s.id != section_id and s.content and len(s.content) > 50]
    if done:
        parts.append("已完成兄弟章节的口径摘要（保持术语与方案一致，不得矛盾）：")
        for s in done[-max_siblings:]:
            head = s.content[:150].replace("\n", " ")
            parts.append(f"- {s.title}：{head}……")
    return "\n".join(parts)


def writing_inputs(project: Project, node: OutlineNode, **overrides) -> Tuple[dict, dict]:
    """
    返回 (select_evidence 参数, build_prompts 参数)。覆盖值为空时回落到章节自身的标题、路径、要求与锁定/排除设置。
    先选资料（检索、企业资料、招标原文）再写正文：生成环节只使用选好的依据。
    """
    o = {k: v for k, v in overrides.items() if v}
    title = o.get("section_title", node.title)
    path = o.get("section_path", node.path or node.title)
    reqs = o.get("requirements", node.requirements)
    instruction = o.get("custom_instruction", "")
    evidence_kw = dict(
        instruction=instruction, section_title=title, section_path=path, requirements=reqs,
        pinned_refs=o.get("pinned_refs", node.pinned_refs), excluded_refs=o.get("excluded_refs", node.excluded_refs),
    )
    prompt_kw = dict(
        section_title=title, section_path=path, requirements=reqs, custom_instruction=instruction,
        facts=project.facts, outline=project.outline, section_id=node.id,
    )
    return evidence_kw, prompt_kw


class SectionGenerator:
    def __init__(self):
        self.llm = llm_client

    def build_prompts(
        self,
        section_title: str,
        section_path: str,
        requirements: List[str],
        evidence: Optional[EvidenceSet] = None,
        custom_instruction: str = "",
        facts: Optional[GlobalFacts] = None,
        outline: Optional[List[OutlineNode]] = None,
        section_id: str = "",
        base_text: Optional[str] = None,
        issues: Optional[List[str]] = None,
        revision_mode: str = "normal",
    ) -> Dict[str, Any]:
        """
        装配提示词（纯函数：不检索、不读资料库，依据全部来自 evidence），返回 {system, user, refs, retrieval_message}。
        base_text / issues：定向修订——在原稿基础上逐条处理问题清单，未涉及的段落保持原样；
        revision_mode 为 alternate / minimal 时附加换方法或只处理阻塞问题的要求（智能完善）。
        """
        facts_obj = facts or GlobalFacts()
        evidence = evidence or EvidenceSet()
        reference_context = evidence.reference_prompt()
        asset_context = f"\n【企业资料（用户录入）】：\n{evidence.asset_context}" if evidence.asset_context else ""
        tender_context = evidence.tender_prompt()

        # ---- 跨章连贯上下文 ----
        coherence = _sibling_context(outline, section_id) if outline and section_id else ""

        # ---- 字数预算 ----
        budget_line = ""
        found_budget = []

        def find_budget(nodes):
            for n in nodes:
                if n.id == section_id and n.word_budget:
                    found_budget.append(n.word_budget)
                find_budget(n.children)

        if outline and section_id:
            find_budget(outline)
        if found_budget:
            budget_line = f"\n【本章节目标正文字数】：约 {found_budget[0]} 字（±15% 以内）"

        req_lines = "\n".join(f"- {r}" for r in requirements) if requirements else "- 详实阐述该模块的具体设计与方案保障"

        if base_text is not None:
            issue_lines = "\n".join(f"{i}. {x}" for i, x in enumerate(issues or [], 1)) or "（无）"
            task = f"""【原稿（在此基础上定向修订）】：
{base_text}

【需逐条处理的问题】：
{issue_lines}

【修订任务】：
逐条处理上述问题，输出修订后的完整正文；未涉及问题的段落保持原样。资料不足以解决的问题用【待填写】或【待核实】占位，不得编造企业事实或承诺数值。直接输出正文，不要解释修改过程。"""
            if revision_mode in REVISION_MODE_HINTS:
                task += f"\n【本轮修订方式】：{REVISION_MODE_HINTS[revision_mode]}"
        else:
            task = """【编写任务】：
请针对上述章节，融合可用的参考资料与企业资料，输出详尽、专业的技术标书正文；企业资质、人员、业绩等事实只能取自上面的企业资料与全局事实。
要求：
1. 方案行文中体现投标主体与核心产品的具体应用与保障（以全局事实为准）；
2. 如涉及架构设计或流转机制，附带一段规范的 ```mermaid 架构图；
3. 严格遵守政企标书语言风格，分层、分点阐述；
4. 直接输出正文内容，无需寒暄。"""

        user_prompt = f"""【当前待撰写章节】：{section_title}
【完整大纲路径】：{section_path or section_title}
{budget_line}

【本项目的全局事实约束（强制遵守，全文一致）】：
{facts_obj.to_constraint_text()}

【本章节须响应的招标要点/评分项】：
{req_lines}
{f'{chr(10)}{tender_context}{chr(10)}' if tender_context else ''}
【人工补充指导意见】：
{custom_instruction if custom_instruction else "无特殊补充，按业内顶级政企技术标标准编写"}
{asset_context}
{f'{chr(10)}{coherence}{chr(10)}' if coherence else ''}
{reference_context if reference_context else '（知识库无高置信参考，请基于业界顶级规范自主设计，严禁套用无关领域方案）'}

{task}"""

        return {
            "system": _select_system_prompt(section_title),
            "user": user_prompt,
            "refs": evidence.refs,
            "retrieval_message": evidence.retrieval_message,
        }

    def draft_section(self, evidence: EvidenceSet, purpose: str = "section_write", **kwargs) -> Dict[str, Any]:
        """同步完整生成（批量撰写、智能完善起草）；依据由调用方先经 select_evidence 选好"""
        built = self.build_prompts(evidence=evidence, **kwargs)
        content = self.llm.chat_completion(system_prompt=built["system"], user_prompt=built["user"], purpose=purpose)
        return {
            "generated_content": strip_title_heading(content, kwargs.get("section_title", "")),
            "references": evidence.ref_records(),
            "retrieval_message": evidence.retrieval_message,
            "mode": self.llm.get_mode(),
        }

    def revise_section(
        self, evidence: EvidenceSet, base_text: str, issues: List[str], revision_mode: str = "normal", **kwargs,
    ) -> Dict[str, Any]:
        """定向修订：在原稿基础上逐条处理问题清单（未涉及的段落保持原样），依据同样由调用方选好"""
        built = self.build_prompts(evidence=evidence, base_text=base_text, issues=issues,
                                   revision_mode=revision_mode, **kwargs)
        content = self.llm.chat_completion(system_prompt=built["system"], user_prompt=built["user"], purpose="section_revise")
        return {
            "generated_content": strip_title_heading(content, kwargs.get("section_title", "")),
            "references": evidence.ref_records(),
            "mode": self.llm.get_mode(),
        }

    async def draft_section_stream(self, evidence: EvidenceSet, **kwargs) -> AsyncGenerator[Dict[str, Any], None]:
        """
        异步流式生成：先 yield 本次依据（知识库片段 + 企业资料，引用面板即时可见），再逐 token yield。
        """
        built = self.build_prompts(evidence=evidence, **kwargs)
        yield {
            "refs": evidence.ref_records(),
            "retrieval_message": evidence.retrieval_message,
            "mode": self.llm.get_mode(),
        }
        async for token in self.llm.chat_completion_stream_async(
            system_prompt=built["system"], user_prompt=built["user"], purpose="section_write"
        ):
            yield {"token": token}


section_generator = SectionGenerator()
