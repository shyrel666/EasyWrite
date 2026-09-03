import re
from typing import List, Dict, Any, Optional
from app.models.schemas import OutlineNode, GlobalFacts, ComplianceCheckReport, DeviationItem
from app.core.llm_client import llm_client

COMPLIANCE_AUDIT_PROMPT = """你是一名资深政府采购和国企招标废标审查专家。
你的任务是审查投标方的技术方案草稿是否完全响应了招标文件的【★号不可偏离红线条款】。

任何一条不满足、存在负偏离或含糊其辞，都会直接导致全盘废标！
请逐条核验，严格按照 JSON 格式输出审查报告：
{
  "passed": true/false,
  "total_star_items": 3,
  "satisfied_star_items": 3,
  "risk_items": [
    {"item": "★号条款内容", "reason": "未找到明确响应依据或存在负偏离", "severity": "HIGH"}
  ],
  "warnings": ["建议在第X章进一步补充证明材料编号"],
  "summary": "整体审查结论"
}
"""

class ComplianceChecker:
    """
    废标项与技术偏离合规核查引擎（借鉴 OpenBidKit / YuduBid 废标审查机制）
    负责：
    1. ★号不可偏离条款点对点匹配核查
    2. 全文负偏离与风险词扫描（如“不支持”、“无法满足”、“暂不提供”）
    3. 全局事实一致性比对（主体名称、法人代表是否一致）
    4. 输出标书“红线体检报告”
    """

    NEGATIVE_KEYWORDS = [
        "不满足", "无法满足", "暂不支持", "无法提供", "存在负偏离",
        "不承诺", "待后续协商", "无法保证", "部分满足"
    ]

    def __init__(self):
        self.llm = llm_client

    def _collect_all_content(self, outline: List[OutlineNode]) -> str:
        texts = []
        def traverse(nodes: List[OutlineNode]):
            for n in nodes:
                if n.content:
                    texts.append(f"【{n.path or n.title}】\n{n.content}")
                traverse(n.children)
        traverse(outline)
        return "\n\n".join(texts)

    def check_compliance(
        self,
        star_items: List[str],
        outline: List[OutlineNode],
        facts: GlobalFacts
    ) -> ComplianceCheckReport:
        full_text = self._collect_all_content(outline)
        
        # 1. 扫描负偏离高危敏感词
        risk_items = []
        warnings = []
        
        for neg_kw in self.NEGATIVE_KEYWORDS:
            if neg_kw in full_text:
                # 定位出现位置
                pos = full_text.find(neg_kw)
                snippet = full_text[max(0, pos-20):min(len(full_text), pos+30)].replace("\n", " ")
                risk_items.append({
                    "item": f"检测到负偏离敏感词汇: '{neg_kw}'",
                    "reason": f"上下文: '...{snippet}...', 极易被评标专家判定为实质性负偏离而废标",
                    "severity": "HIGH"
                })

        # 2. ★号条款点对点响应核查
        satisfied_count = 0
        for star in star_items:
            clean_star = star.replace("★", "").replace("▲", "").strip()
            # 提取核心关键词（如“自主知识产权”、“等级保护”、“国密”）
            key_terms = [t for t in re.split(r'[,，。；;\s]+', clean_star) if len(t) >= 4]
            
            matched = False
            if key_terms:
                # 检查标书正文中是否涵盖这些核心要求
                hits = [term for term in key_terms if term in full_text]
                if len(hits) >= max(1, len(key_terms) // 2):
                    matched = True
            elif clean_star[:8] in full_text:
                matched = True

            if matched:
                satisfied_count += 1
            else:
                risk_items.append({
                    "item": star,
                    "reason": "在技术方案正文中未检测到针对该★号条款的明确响应论述或承诺",
                    "severity": "HIGH"
                })

        # 3. 全局事实一致性校验（公司名称是否混淆）
        if facts.company_name and facts.company_name != "标书联合体（默认供应商）":
            if facts.company_name not in full_text:
                warnings.append(f"标书正文未显式提及全局事实设定的企业全称【{facts.company_name}】，建议核对封面及正文落款。")

        passed = (len(risk_items) == 0 and satisfied_count == len(star_items))

        summary_text = (
            f"本次共核查 {len(star_items)} 项★号不可偏离条款，其中已完全响应 {satisfied_count} 项，"
            f"发现高危风险项 {len(risk_items)} 个，轻度提醒 {len(warnings)} 个。"
            + ("【结论：合规自检通过，未发现废标红线】" if passed else "【结论：存在高危废标风险，必须在交卷前整改！】")
        )

        return ComplianceCheckReport(
            passed=passed,
            total_star_items=len(star_items),
            satisfied_star_items=satisfied_count,
            risk_items=risk_items,
            warnings=warnings,
            summary=summary_text
        )

compliance_checker = ComplianceChecker()
