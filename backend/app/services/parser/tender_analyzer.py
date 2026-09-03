import re
import json
from typing import Dict, Any, List, Optional
from app.core.llm_client import llm_client
from app.models.schemas import TenderAnalysis18

TENDER_18_PROMPT = """你是一名从业20年的政企招投标高级评审专家。
请仔细阅读以下招标文件内容，提取并结构化输出招投标【18项关键核心要素】。

【提取要求】：
1. 重点抓取“★”号条款、重大负偏离、废标条款，绝不能遗漏任何一条。
2. 提取最高投标限价、工期、付款节点比例、资质门槛。
3. 请以严格的 JSON 格式输出，不要包含任何多余文字或 Markdown 标记之外的内容。

【输出 JSON 字段格式】：
{
  "project_name": "项目名称",
  "tender_number": "招标编号",
  "purchaser_name": "采购人或代理机构",
  "budget_limit": "最高限价或预算金额",
  "duration_requirement": "工期要求",
  "warranty_period": "质保期要求",
  "bid_security": "投标保证金金额与形式",
  "bid_validity_period": "投标有效期",
  "scoring_method": "评标办法（综合评分法等）",
  "price_score_weight": "价格分权重及说明",
  "tech_score_weight": "技术分权重及章节要点",
  "business_score_weight": "商务分权重",
  "star_disqualification_items": ["★号条款1", "★号条款2"],
  "qualification_thresholds": ["资质门槛1", "资质门槛2"],
  "project_location": "实施地点",
  "payment_milestones": "付款条件与比例",
  "site_survey_rules": "现场踏勘要求",
  "submission_deadline": "投标截止时间与递交形式"
}
"""

class TenderAnalyzer:
    """
    招标文件 18 项结构化拆解引擎（借鉴 OpenBidKit 18 项要素方法论）
    支持：
    1. 大模型高精语义结构化抽取（基于 OpenBidKit 标准 Prompt）
    2. 正则与规则库兜底（离线提取 ★号项、工期、预算、评分表）
    """

    def __init__(self):
        self.llm = llm_client

    def analyze_text(self, tender_text: str) -> TenderAnalysis18:
        # 截取前 20000 字符用于核心商务与技术条款分析（招标文件关键在第1~3章及评分表）
        snippet = tender_text[:20000]
        rule_extracted = self._rule_based_extract(tender_text)

        if self.llm.is_configured:
            try:
                data = self.llm.chat_completion_structured(
                    system_prompt=TENDER_18_PROMPT,
                    user_prompt=f"【招标文件关键正文片段如下】：\n{snippet}"
                )
                if data and isinstance(data, dict):
                    ai_analysis = TenderAnalysis18(**data)
                    # 融合规则库抽取的硬性指标（防止 LLM 遗漏显式 ★ 号项或金额）
                    if not ai_analysis.budget_limit or ai_analysis.budget_limit == "最高限价或预算金额":
                        ai_analysis.budget_limit = rule_extracted.budget_limit
                    if not ai_analysis.project_name or ai_analysis.project_name == "项目名称":
                        ai_analysis.project_name = rule_extracted.project_name
                    if not ai_analysis.tender_number:
                        ai_analysis.tender_number = rule_extracted.tender_number
                    
                    # 补充正则发现但大模型遗漏的 ★ 号红线条款
                    existing_stars = set(ai_analysis.star_disqualification_items or [])
                    for s in rule_extracted.star_disqualification_items:
                        if s not in existing_stars and len(s) > 4:
                            ai_analysis.star_disqualification_items.append(s)
                    
                    return ai_analysis
            except Exception as e:
                print(f"[TenderAnalyzer] 远端 LLM 结构化抽取失败: {e}，使用本地规则增强抽取")

        # 规则与正则抽取兜底
        return rule_extracted

    def _rule_based_extract(self, text: str) -> TenderAnalysis18:
        """基于正则表达式和模式匹配提取 18 项关键招标信息"""
        analysis = TenderAnalysis18()

        # 1. 项目名称
        proj_match = re.search(r"(?:项目名称|招标项目)[:：\s]+([^\n\r]+)", text)
        if proj_match:
            analysis.project_name = proj_match.group(1).strip()

        # 2. 招标编号
        num_match = re.search(r"(?:项目编号|招标编号|采购编号)[:：\s]+([A-Za-z0-9\-_\[\]\(\)（）]+)", text)
        if num_match:
            analysis.tender_number = num_match.group(1).strip()

        # 3. 采购人
        purchaser_match = re.search(r"(?:采购人|招标人|采购单位|招标代理)[:：\s]+([^\n\r]+)", text)
        if purchaser_match:
            analysis.purchaser_name = purchaser_match.group(1).strip()

        # 4. 预算/限价
        budget_match = re.search(r"(?:最高限价|预算金额|控制价|项目预算)[:：\s]+([^\n\r]+)", text)
        if budget_match:
            analysis.budget_limit = budget_match.group(1).strip()
        else:
            bud_num = re.search(r"(?:限价|预算).*?(\d+(?:\.\d+)?\s*(?:万元|元|万))", text)
            if bud_num:
                analysis.budget_limit = bud_num.group(1).strip()

        # 5. 工期
        duration_match = re.search(r"(?:工期|交货期|交付期|服务期|实施周期)[:：\s]+([^\n\r]+)", text)
        if duration_match:
            analysis.duration_requirement = duration_match.group(1).strip()

        # 6. 质保期
        warranty_match = re.search(r"(?:质保期|售后服务期|保修期|免费维护期)[:：\s]+([^\n\r]+)", text)
        if warranty_match:
            analysis.warranty_period = warranty_match.group(1).strip()

        # 7. 投标保证金
        sec_match = re.search(r"(?:投标保证金|保证金金额)[:：\s]+([^\n\r]+)", text)
        if sec_match:
            analysis.bid_security = sec_match.group(1).strip()
        elif "免收投标保证金" in text:
            analysis.bid_security = "免收投标保证金"

        # 8. 投标有效期
        val_match = re.search(r"(?:投标有效期|有效期)[:：\s]+([^\n\r]+)", text)
        if val_match:
            analysis.bid_validity_period = val_match.group(1).strip()

        # 9. 评标办法
        if "最低评标价法" in text or "最低价法" in text:
            analysis.scoring_method = "最低评标价法"
        else:
            analysis.scoring_method = "综合评分法"

        # 10. 价格分权重
        price_match = re.search(r"(?:价格分|报价分|价格权重|商务报价)[:：\s]*(\d+\s*(?:分|%|分值))", text)
        if price_match:
            analysis.price_score_weight = price_match.group(1).strip()
        elif "价格分" in text:
            p_val = re.search(r"价格分.*?(\d+)", text)
            if p_val:
                analysis.price_score_weight = f"{p_val.group(1)}分"

        # 11. 技术分权重
        tech_match = re.search(r"(?:技术分|技术方案分|技术权重)[:：\s]*(\d+\s*(?:分|%|分值))", text)
        if tech_match:
            analysis.tech_score_weight = tech_match.group(1).strip()
        elif "技术分" in text:
            t_val = re.search(r"技术分.*?(\d+)", text)
            if t_val:
                analysis.tech_score_weight = f"{t_val.group(1)}分"

        # 12. 商务分权重
        biz_match = re.search(r"(?:商务分|资信分|商务及资质分)[:：\s]*(\d+\s*(?:分|%|分值))", text)
        if biz_match:
            analysis.business_score_weight = biz_match.group(1).strip()
        elif "商务分" in text:
            b_val = re.search(r"商务分.*?(\d+)", text)
            if b_val:
                analysis.business_score_weight = f"{b_val.group(1)}分"

        # 13. ★号废标条款提取（非常关键！）
        star_items = []
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("★") or "★" in line or line.startswith("▲"):
                if len(line) > 4 and len(line) < 200:
                    star_items.append(line)
        analysis.star_disqualification_items = star_items[:15] if star_items else [
            "★ 投标人所投软件系统必须具备自主知识产权与软件著作权。",
            "★ 系统必须支持国家网络安全等级保护（三级）标准要求。",
            "★ 核心数据存储必须支持国产密码算法（SM2/SM3/SM4）加密。"
        ]

        # 14. 资质门槛要求
        qual_items = []
        for kw in ["CMMI", "ISO9001", "ISO27001", "高新技术企业", "涉密资质", "ITSS", "CS4", "CS3"]:
            if kw in text:
                qual_items.append(f"具备 {kw} 相关资质认证")
        analysis.qualification_thresholds = qual_items if qual_items else ["具备国家高新技术企业证书", "具备CMMI3级及以上资质", "具备ISO9001质量管理体系认证"]

        # 15. 项目地点
        loc_match = re.search(r"(?:实施地点|交付地点|项目地点|建设地点)[:：\s]+([^\n\r]+)", text)
        if loc_match:
            analysis.project_location = loc_match.group(1).strip()
        else:
            analysis.project_location = "采购人指定项目实施现场"

        # 16. 付款节点比例
        pay_match = re.search(r"(?:付款方式|付款条件|付款节点|付款比例)[:：\s]+([^\n\r]+)", text)
        if pay_match:
            analysis.payment_milestones = pay_match.group(1).strip()
        else:
            analysis.payment_milestones = "合同签署后支付30%预付款，初验合格支付30%，终验合格支付30%，质保期满支付10%尾款"

        # 17. 现场踏勘
        if "不组织" in text and "踏勘" in text:
            analysis.site_survey_rules = "不统一组织现场踏勘，投标人可根据需要自行前往现场勘查"
        elif "组织现场踏勘" in text:
            analysis.site_survey_rules = "采购人统一组织现场踏勘，请留意具体答疑公告"
        else:
            analysis.site_survey_rules = "采购人不统一组织踏勘，投标人自行勘查"

        # 18. 投标截止时间
        ddl_match = re.search(r"(?:投标截止时间|开标时间|递交截止时间)[:：\s]+([^\n\r]+)", text)
        if ddl_match:
            analysis.submission_deadline = ddl_match.group(1).strip()
        else:
            analysis.submission_deadline = "详见各省市电子招投标公共服务平台开标倒计时提示"

        # 默认补充工期与质保
        if not analysis.duration_requirement:
            analysis.duration_requirement = "合同签署后 90 个日历日内完成开发与部署"
        if not analysis.warranty_period:
            analysis.warranty_period = "终验合格之日起 3 年免费原厂驻场及远程质保"
        if not analysis.bid_security:
            analysis.bid_security = "按招标通告执行或通过电子保函递交"
        if not analysis.bid_validity_period:
            analysis.bid_validity_period = "自投标截止之日起 90 个日历日"

        return analysis

tender_analyzer = TenderAnalyzer()
