"""
招标文件 18 项结构化拆解引擎（LLM 优先 + 规则补充）。

重写要点（对照旧版的核心修复）：
1. 哨兵值纪律（借鉴 OpenBidKit）：字段级未提及 = "未提及"，绝不用编造的默认值顶替。
   旧版在正则未命中时会凭空捏造★条款/预算/资质/付款比例——对标书系统是致命的正确性风险。
2. 长文分段抽取 + 结果合并：不再粗暴截断前 2 万字（评分表常在文件后部）。
3. 规则库只做"补充"不做"兜底编造"：正则找到就填，找不到就留哨兵值。
4. 全程透传 extraction_mode（llm / rules / mock），前端明确展示数据来源。
"""
import logging
import re
from typing import List, Optional

from app.core.llm_client import llm_client
from app.models.schemas import TenderAnalysis18

logger = logging.getLogger("easywrite.tender")

NOT_MENTIONED = "未提及"

TENDER_18_SYSTEM = """你是一名从业20年的政企招投标高级评审专家。
请仔细阅读招标文件内容，提取以下 18 项关键要素。

【输出纪律】：
1. 只依据文件原文提取，绝不推测或编造；文件未提及的字段一律填 "未提及"。
2. "★"号条款、废标条款、重大负偏离条款逐条完整摘录原文，一条都不能遗漏；
   摘录时保留条款原文表述（可截断至关键句），并保留★或▲标记。
3. 评分办法需提取：评标方法、价格/技术/商务三维分值与关键评分项名称。
4. 只输出一个严格合法的 JSON 对象，禁止输出任何解释文字或 markdown 代码块。

【输出 JSON 字段格式】（只修改 value，禁止修改 key 和结构）：
{
  "project_name": "项目名称",
  "tender_number": "招标编号",
  "purchaser_name": "采购人或代理机构",
  "budget_limit": "最高限价或预算金额",
  "duration_requirement": "工期要求",
  "warranty_period": "质保期要求",
  "bid_security": "投标保证金金额与形式",
  "bid_validity_period": "投标有效期",
  "scoring_method": "评标办法",
  "price_score_weight": "价格分权重及说明",
  "tech_score_weight": "技术分权重及章节要点",
  "business_score_weight": "商务分权重",
  "star_disqualification_items": ["★号条款原文1", "★号条款原文2"],
  "qualification_thresholds": ["资质门槛1", "资质门槛2"],
  "project_location": "实施地点",
  "payment_milestones": "付款条件与比例",
  "site_survey_rules": "现场踏勘要求",
  "submission_deadline": "投标截止时间与递交形式"
}"""

# 分段参数：单段 ~12000 字（DeepSeek 64K token 上下文富余），段落对齐
SEGMENT_CHAR_LIMIT = 12000


class TenderAnalyzer:
    def __init__(self):
        self.llm = llm_client

    # ---------------- 对外入口 ----------------

    def analyze_text(self, tender_text: str, filename_hint: str = "") -> TenderAnalysis18:
        """
        LLM 优先：分段抽取 → 合并；规则库补充显式★条款与金额。
        返回结果附带 extraction_mode 字段（llm / rules）。
        """
        text = (tender_text or "").strip()
        if not text:
            result = TenderAnalysis18()
            if filename_hint:
                result.project_name = re.sub(r"\.(docx?|pdf|wps)$", "", filename_hint, flags=re.I)
            result.extraction_mode = "rules"
            result.extraction_note = "招标文件内容为空，未能提取要素"
            return result

        rule_result = self._rule_based_extract(text)

        if self.llm.is_configured:
            merged = self._llm_segmented_extract(text)
            if merged is not None:
                self._supplement_with_rules(merged, rule_result)
                merged.extraction_mode = "llm"
                return merged
            # LLM 调用失败：规则结果 + 明确提示（不再伪装成功）
            rule_result.extraction_mode = "rules"
            rule_result.extraction_note = "大模型抽取失败，当前为正则规则抽取结果（覆盖度有限），建议检查 API 配置后重试"
            return rule_result

        rule_result.extraction_mode = "rules"
        rule_result.extraction_note = "未配置大模型，当前为正则规则抽取结果（覆盖度有限）"
        return rule_result

    # ---------------- LLM 分段抽取 ----------------

    def _llm_segmented_extract(self, text: str) -> Optional[TenderAnalysis18]:
        segments = self._split_segments(text)
        results = []
        for i, seg in enumerate(segments):
            data = self.llm.chat_completion_structured(
                system_prompt=TENDER_18_SYSTEM,
                user_prompt=f"【招标文件正文片段 {i + 1}/{len(segments)}】：\n{seg}",
                temperature=0.1,
            )
            if isinstance(data, dict):
                try:
                    results.append(TenderAnalysis18(**{k: v for k, v in data.items() if k in TenderAnalysis18.model_fields()}))
                except Exception as e:
                    logger.warning("分段 %d 结构化解析失败: %s", i + 1, e)
        if not results:
            return None
        if len(results) == 1:
            return results[0]
        return self._merge_results(results)

    @staticmethod
    def _split_segments(text: str) -> List[str]:
        """按段落对齐切分，避免截断句子/条款"""
        paragraphs = text.split("\n")
        segments, buf, size = [], [], 0
        for p in paragraphs:
            if size + len(p) > SEGMENT_CHAR_LIMIT and buf:
                segments.append("\n".join(buf))
                buf, size = [], 0
            buf.append(p)
            size += len(p) + 1
        if buf:
            segments.append("\n".join(buf))
        return segments or [text]

    @staticmethod
    def _merge_results(results: List[TenderAnalysis18]) -> TenderAnalysis18:
        """多段合并：字符串字段取首个非哨兵值，列表字段去重并集，★条款优先保留"""
        merged = TenderAnalysis18()
        for field in TenderAnalysis18.model_fields():
            if field in ("extraction_mode", "extraction_note"):
                continue
            values = [getattr(r, field) for r in results]
            if isinstance(values[0], list):
                seen, combined = set(), []
                for v in values:
                    for item in v or []:
                        key = re.sub(r"\s+", "", str(item))[:80]
                        if key and key not in seen:
                            seen.add(key)
                            combined.append(item)
                setattr(merged, field, combined)
            else:
                for v in values:
                    if v and v.strip() and v.strip() != NOT_MENTIONED and "招标文件正文片段" not in v:
                        setattr(merged, field, v)
                        break
        return merged

    # ---------------- 规则补充（不编造） ----------------

    @staticmethod
    def _supplement_with_rules(target: TenderAnalysis18, rules: TenderAnalysis18):
        """规则库只补充 LLM 明确漏掉的显式★条款与硬性金额，不覆盖 LLM 结论"""
        existing_stars = {re.sub(r"\s+", "", s)[:60] for s in (target.star_disqualification_items or [])}
        for s in rules.star_disqualification_items or []:
            key = re.sub(r"\s+", "", s)[:60]
            if key and key not in existing_stars:
                target.star_disqualification_items.append(s)
                existing_stars.add(key)
        # 空列表字段用规则结果填充（规则未命中则为空，保持诚实）
        for field in ("star_disqualification_items", "qualification_thresholds"):
            if not getattr(target, field) and getattr(rules, field):
                setattr(target, field, getattr(rules, field))

    # ---------------- 规则抽取（只提取，不编造） ----------------

    def _rule_based_extract(self, text: str) -> TenderAnalysis18:
        a = TenderAnalysis18()

        def take(pattern: str, flags: int = 0) -> Optional[str]:
            m = re.search(pattern, text, flags)
            return m.group(1).strip() if m else None

        a.project_name = take(r"(?:项目名称|招标项目名称)[:：\s]+([^\n\r，。；]+)") or ""
        a.tender_number = take(r"(?:项目编号|招标编号|采购编号|标段编号)[:：\s]+([A-Za-z0-9\-_（）()\[\]]+)") or ""
        a.purchaser_name = take(r"(?:采购人|招标人|采购单位)[:：\s]+([^\n\r，。；]+)") or ""
        a.budget_limit = take(r"(?:最高限价|预算金额|控制价|项目预算|采购预算)[:：\s]+([^\n\r，。；]+)") or ""
        a.duration_requirement = take(r"(?:工期要求|工期|交货期|交付期|服务期|实施周期|建设期)[:：\s]+([^\n\r。；]+)") or ""
        a.warranty_period = take(r"(?:质保期|售后服务期|保修期|免费维护期)[:：\s]+([^\n\r。；]+)") or ""
        a.bid_security = take(r"(?:投标保证金|保证金)[:：\s]+([^\n\r。；]+)") or ""
        a.bid_validity_period = take(r"投标有效期[:：\s]+([^\n\r。；]+)") or ""
        a.scoring_method = "综合评分法" if "综合评分" in text else ("最低评标价法" if "最低评标价" in text else "")
        a.price_score_weight = take(r"(?:价格分|报价分|价格权重)[:：\s]*(\d+\s*(?:分|%))") or ""
        a.tech_score_weight = take(r"(?:技术分|技术方案分|技术权重)[:：\s]*(\d+\s*(?:分|%))") or ""
        a.business_score_weight = take(r"(?:商务分|资信分|商务及资信分)[:：\s]*(\d+\s*(?:分|%))") or ""
        a.project_location = take(r"(?:实施地点|交付地点|项目地点|建设地点|服务地点)[:：\s]+([^\n\r。；]+)") or ""
        a.payment_milestones = take(r"(?:付款方式|付款条件|付款节点|付款比例|支付方式)[:：\s]+([^\n\r。；]+)") or ""
        a.submission_deadline = take(r"(?:投标截止时间|递交截止时间|开标时间|递交投标文件截止时间)[:：\s]+([^\n\r。；]+)") or ""

        # 现场踏勘：只归纳原文出现的表述
        if "踏勘" in text:
            a.site_survey_rules = "招标文件包含现场踏勘相关条款（详见原文）"

        # ★号条款：逐行摘录原文（关键红线，宁多勿漏）
        stars = []
        for line in text.split("\n"):
            line = line.strip()
            if ("★" in line or line.startswith("▲")) and 4 < len(line) < 200:
                stars.append(line)
        a.star_disqualification_items = stars[:20]

        # 资质门槛：仅在原文出现关键词时记录
        quals = []
        for kw in ["CMMI", "ISO9001", "ISO27001", "ISO20000", "高新技术企业", "涉密", "ITSS", "CS3", "CS4", "CCRC", "信息系统建设和服务能力"]:
            if kw in text:
                quals.append(f"文件提及资质要求：{kw}（详见原文资质条款）")
        a.qualification_thresholds = quals

        return a


tender_analyzer = TenderAnalyzer()
