"""
章节撰写引擎（RAG 混合检索 + 跨章连贯上下文 + 事实完备纪律）。

对比旧版 bid_generator 的核心升级：
1. 检索走 retrieval_service 完整管线（多查询→双路召回→RRF→LLM重排→阈值→父级扩展），
   引用全程溯源，支持锁定/排除人工在环；旧版是 TF-IDF 直取 top3、无阈值（垃圾也注入）
2. 跨章连贯：注入父章节与已完成兄弟章节的摘要，防止"前文K8s后文Swarm"式矛盾
3. 事实完备模式：omit/placeholder 两档，未提供的事实不再被模型自由编造
4. 领域专家 System Prompt 按章节主题自动装配（保留）
5. SSE 流式走 AsyncOpenAI，不阻塞事件循环
"""
import asyncio
import re
import logging
from typing import Dict, Any, List, Optional, AsyncGenerator

from app.core.llm_client import llm_client
from app.models.schemas import OutlineNode, GlobalFacts
from app.services.rag.retriever import retrieval_service
from app.services.assets.asset_manager import asset_manager

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


class SectionGenerator:
    def __init__(self):
        self.llm = llm_client

    def build_prompts(
        self,
        section_title: str,
        section_path: str,
        requirements: List[str],
        custom_instruction: str = "",
        facts: Optional[GlobalFacts] = None,
        outline: Optional[List[OutlineNode]] = None,
        section_id: str = "",
        project_context: str = "",
        pinned_refs: Optional[List[str]] = None,
        excluded_refs: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """装配检索 + 提示词，返回 {system, user, refs, retrieval_message}"""
        facts_obj = facts or GlobalFacts()

        # ---- RAG 混合检索（锁定/排除人工在环） ----
        retrieval = retrieval_service.retrieve(
            query=f"{section_title} {custom_instruction}",
            section_title=section_title,
            section_path=section_path,
            requirements=requirements,
            project_context=project_context,
            top_k=4,
            pinned_ids=pinned_refs,
            excluded_ids=excluded_refs,
        )
        refs = retrieval["refs"]
        reference_context = retrieval_service.build_reference_prompt(refs)

        # ---- 企业中台资产匹配 ----
        matched_asset = asset_manager.match_assets_for_section(section_title, requirements)
        asset_context = (
            f"\n【企业中台权威资产库（优先复用与论述）】：\n{matched_asset['context_text']}"
            if matched_asset.get("context_text") else ""
        )

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

        user_prompt = f"""【当前待撰写章节】：{section_title}
【完整大纲路径】：{section_path or section_title}
{budget_line}

【本项目的全局事实约束（强制遵守，全文一致）】：
{facts_obj.to_constraint_text()}

【本章节须响应的招标要点/评分项】：
{req_lines}

【人工补充指导意见】：
{custom_instruction if custom_instruction else "无特殊补充，按业内顶级政企技术标标准编写"}
{asset_context}
{f'{chr(10)}{coherence}{chr(10)}' if coherence else ''}
{reference_context if reference_context else '（知识库无高置信参考，请基于业界顶级规范自主设计，严禁套用无关领域方案）'}

【编写任务】：
请针对上述章节，融合可用的参考资料与企业资产中的成熟经验，输出详尽、专业的技术标书正文。
要求：
1. 方案行文中体现投标主体与核心产品的具体应用与保障（以全局事实为准）；
2. 如涉及架构设计或流转机制，附带一段规范的 ```mermaid 架构图；
3. 严格遵守政企标书语言风格，分层、分点阐述；
4. 直接输出正文内容，无需寒暄。"""

        return {
            "system": _select_system_prompt(section_title),
            "user": user_prompt,
            "refs": refs,
            "retrieval_message": retrieval.get("message", ""),
        }

    def draft_section(self, **kwargs) -> Dict[str, Any]:
        """同步完整生成（后台批量任务使用）"""
        built = self.build_prompts(**kwargs)
        content = self.llm.chat_completion(system_prompt=built["system"], user_prompt=built["user"], purpose="section_write")
        return {
            "generated_content": strip_title_heading(content, kwargs.get("section_title", "")),
            "references": built["refs"],
            "retrieval_message": built["retrieval_message"],
            "mode": self.llm.get_mode(),
        }

    async def draft_section_stream(self, **kwargs) -> AsyncGenerator[Dict[str, Any], None]:
        """
        异步流式生成：先 yield 检索元数据（引用面板即时可见），再逐 token yield。
        """
        # 检索 + LLM 重排是同步阻塞调用，放到线程中执行，避免卡住事件循环（期间的自动保存等请求）
        built = await asyncio.to_thread(self.build_prompts, **kwargs)
        yield {
            "refs": built["refs"],
            "retrieval_message": built["retrieval_message"],
            "mode": self.llm.get_mode(),
        }
        async for token in self.llm.chat_completion_stream_async(
            system_prompt=built["system"], user_prompt=built["user"], purpose="section_write"
        ):
            yield {"token": token}


section_generator = SectionGenerator()
