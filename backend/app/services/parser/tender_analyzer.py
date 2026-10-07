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
from typing import Dict, List, Optional, Tuple

from app.core.llm_client import llm_client
from app.models.schemas import TenderAnalysis18
from app.services.parser.scoring_extractor import apply_to_analysis, extract_scoring_items

logger = logging.getLogger("easywrite.tender")

NOT_MENTIONED = "未提及"

TENDER_18_SYSTEM = """你是一名从业20年的政企招投标高级评审专家。
请仔细阅读招标文件内容，提取以下 18 项关键要素。

【输出纪律】：
1. 只依据文件原文提取，绝不推测或编造；你看到的可能只是文件的一个片段，片段中没有的信息一律只填 "未提及" 四个字，不要附加任何解释。
2. 招标文件常用 ★/※/▲ 等标记条款，并在文中定义其含义（如"※标注的…不满足按无效投标处理"、
   "★标注的…不满足按评标因素扣分"），必须以文件自身的定义为准：
   - star_disqualification_items：不满足即无效投标/废标的条款（实质性要求、废标条款），逐条摘录原文，一条都不能遗漏；
   - important_items：不满足仅按评分办法扣分的重要条款，逐条摘录原文；
   摘录时保留条款原文表述（可截断至关键句）与标记符号。
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
  "star_disqualification_items": ["不满足即废标的条款原文1", "条款原文2"],
  "important_items": ["不满足扣分的重要条款原文1"],
  "qualification_thresholds": ["资质门槛1", "资质门槛2"],
  "project_location": "实施地点",
  "payment_milestones": "付款条件与比例",
  "site_survey_rules": "现场踏勘要求",
  "submission_deadline": "投标截止时间与递交形式"
}"""

# 分段参数：单段 ~12000 字（DeepSeek 64K token 上下文富余），段落对齐
SEGMENT_CHAR_LIMIT = 12000

# 大模型可写入的字段（评分细则/标记定义等由确定性规则产出，不接受模型输出）
LLM_FIELDS = set(TenderAnalysis18.model_fields) - {
    "scoring_items", "scoring_note", "target_package", "marker_legend", "extraction_mode", "extraction_note",
}

# 条款标记：含义以招标文件自身的说明句为准（☆△# 只在文件定义了含义时才起作用）
CLAUSE_MARKERS = "★※▲◆☆△#"
INVALID_RE = re.compile(r"无效投标|无效响应|投标无效|废标|否决|实质性|投标被拒绝|拒绝其投标|不通过符合性审查")
DEDUCT_RE = re.compile(r"评标因素|评分|扣分|扣除|酌情|重要")
# 「不作为认定无效投标的依据」之类的否定说明不算废标
NEGATED_INVALID = re.compile(r"不(?:作为|属于|视为|构成|按)[^，。；;]{0,12}(?:无效|废标|实质|否决)[^，。；;]*")
# 规则抽取的字段值里出现下一个字段标签时截断（PDF 同一行并排的两个字段被合成一行）
NEXT_FIELD = re.compile(
    r"(?<=\S)\s*(?=(?:项目编号|招标编号|采购编号|项目名称|预算金额|最高限价"
    r"|(?:采购人|采购单位|招标人|采购代理机构|代理机构)(?:名称|地址|联系人|联系电话|联系方式|电话|邮箱)?"
    r"|联系人|联系电话|联系方式|地\s*址)\s*[:：])"
)
_OPEN_Q, _CLOSE_Q =r"[“\"「‘'（(]?\s*", r"\s*[”\"」’'）)]?"
# 定义句中的标记：「“※”标注的」「★代表」「★表示」「凡标有★条款」「标“★”号项」
LEGEND_MARK = re.compile(
    r"(?:(?:标有|标注有|带有|打有|凡标)\s*" + _OPEN_Q + r"|标\s*[“\"「‘'（(]\s*)([" + CLAUSE_MARKERS + "])" + _CLOSE_Q
    + r"|" + _OPEN_Q + "([" + CLAUSE_MARKERS + "])" + _CLOSE_Q + r"\s*(?:号|符号)?\s*的?\s*(?:标注|标识|标记|代表|表示)"
)


def coerce_llm_fields(data: dict) -> dict:
    """
    把模型输出规整为 TenderAnalysis18 可接受的类型：数字/列表/对象转字符串、字符串转列表；
    「未提及（本片段仅…）」之类带解释的哨兵值归一为「未提及」。单个字段类型不对不应丢掉整段结果。
    """
    out = {}
    for key, value in data.items():
        if key not in LLM_FIELDS or value is None:
            continue
        expects_list = TenderAnalysis18.model_fields[key].annotation in (List[str], list)
        if expects_list:
            if isinstance(value, str):
                value = [value] if value.strip() and not value.strip().startswith(NOT_MENTIONED) else []
            elif isinstance(value, list):
                value = [str(v).strip() for v in value
                         if str(v).strip() and not str(v).strip().startswith(NOT_MENTIONED)]
            else:
                continue
        else:
            if isinstance(value, list):
                value = "；".join(str(v) for v in value)
            elif isinstance(value, dict):
                value = "；".join(f"{k}：{v}" for k, v in value.items())
            value = str(value).strip()
            if value.startswith(NOT_MENTIONED):
                value = NOT_MENTIONED
        out[key] = value
    return out


def marker_semantics(text: str) -> Dict[str, Tuple[str, str]]:
    """
    解析标记定义句：「“※”标注的…为符合性审查中的实质性要求，若不满足按无效投标处理」→ {"※": ("invalid", 原句)}；
    「“★”标注的…若不满足将按照评标因素中相关规定处理」→ {"★": ("deduct", 原句)}。
    文件未定义时按国内通行惯例：★ 视为实质性条款（invalid），▲ 视为重要条款（deduct）。
    """
    found: Dict[str, Tuple[str, str]] = {}
    for line in (text or "").split("\n"):
        # 一句里可能依次定义多个标记（「★代表实质性指标…，☆代表优质优价指标，#代表重要指标」），逐段判断
        marks = list(LEGEND_MARK.finditer(line))
        for k, m in enumerate(marks):
            marker = m.group(1) or m.group(2)
            if marker in found:
                continue
            clause = line[m.start():marks[k + 1].start() if k + 1 < len(marks) else len(line)]
            sentence = line.strip()[:120] if len(marks) == 1 else clause.strip(" ，,；;")[:120]
            plain = NEGATED_INVALID.sub("", clause)
            if INVALID_RE.search(plain):
                found[marker] = ("invalid", sentence)
            elif DEDUCT_RE.search(plain):
                found[marker] = ("deduct", sentence)
    found.setdefault("★", ("invalid", ""))
    found.setdefault("▲", ("deduct", ""))
    return found


def is_marker_reference(line: str) -> bool:
    """标记定义句（「※标注的…」「★代表…」「凡标有★的条款…」）或「（※）号标注的部分」这类引用说明本身不是条款"""
    return "标注" in line or bool(LEGEND_MARK.search(line))


class TenderAnalyzer:
    def __init__(self):
        self.llm = llm_client

    # ---------------- 对外入口 ----------------

    def analyze_document(self, parsed: dict, filename_hint: str = "") -> TenderAnalysis18:
        """
        解析后的 .docx 招标文件：全文走 18 项抽取，评分表走确定性结构化抽取。
        parsed 为 WordDocumentParser.parse_docx() 的返回值。
        """
        analysis = self.analyze_text(parsed.get("full_text", ""), filename_hint=filename_hint)
        return apply_to_analysis(analysis, extract_scoring_items(parsed.get("tables", [])))

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
                purpose="tender_extract",
            )
            if isinstance(data, dict):
                try:
                    results.append(TenderAnalysis18(**coerce_llm_fields(data)))
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
        for field in LLM_FIELDS:
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
        # 模型常把标记定义句本身（「本篇“※”标注的…」）当成条款摘录，剔除
        target.star_disqualification_items = [s for s in target.star_disqualification_items if not is_marker_reference(s)]
        target.important_items = [s for s in target.important_items if not is_marker_reference(s)]
        existing_important = {re.sub(r"\s+", "", s)[:60] for s in (target.important_items or [])}
        for s in rules.important_items or []:
            key = re.sub(r"\s+", "", s)[:60]
            if key and key not in existing_important and key not in existing_stars:
                target.important_items.append(s)
                existing_important.add(key)
        # 空列表字段用规则结果填充（规则未命中则为空，保持诚实）
        for field in ("star_disqualification_items", "qualification_thresholds"):
            if not getattr(target, field) and getattr(rules, field):
                setattr(target, field, getattr(rules, field))
        target.marker_legend = rules.marker_legend
        # 模型没给出（空/未提及）而规则命中的单值字段用规则结果补齐（如项目名称、截止时间）
        for field in LLM_FIELDS:
            value = getattr(target, field)
            rule_value = getattr(rules, field)
            if isinstance(value, str) and (not value.strip() or value.strip() == NOT_MENTIONED) \
                    and isinstance(rule_value, str) and rule_value.strip():
                setattr(target, field, rule_value)

    # ---------------- 规则抽取（只提取，不编造） ----------------

    def _rule_based_extract(self, text: str) -> TenderAnalysis18:
        a = TenderAnalysis18()

        def take(*patterns: str) -> Optional[str]:
            """
            按顺序取第一个像样的命中：表格行拼接片段（含 |）与只有标签的残值（如「包1：」）宁可留空；
            同一行并排的下一个字段（PDF 中「项目名称：XX项目编号：YY」）截掉
            """
            for pattern in patterns:
                for m in re.finditer(pattern, text):
                    value = NEXT_FIELD.split(m.group(1).strip(), maxsplit=1)[0].strip()
                    if "|" in value or len(value) < 2 or value[-1] in "：:" or re.fullmatch(r"[（(][^）)]*[）)]", value):
                        continue
                    return value
            return None

        a.project_name = take(r"(?:项目名称|招标项目名称)[:：\s]+([^\n\r，。；]+)") or ""
        a.tender_number = take(r"(?:项目编号|招标编号|采购编号|标段编号)[:：\s]+([A-Za-z0-9\-_（）()\[\]]+)") or ""
        # 只认冒号、表格分隔（「采购人名称 | XX」）与「采购人信息 / 名称：XX」：「采购人 承担连带责任」是正文
        a.purchaser_name = take(
            r"(?:采购人|招标人|采购单位)(?:名称)?\s*[:：]\s*([^\n\r，。；|]+)",
            r"(?:采购人|招标人|采购单位)(?:名称)?\s*\|\s*([^\n\r，。；|]+)",
            r"采购人信息\s*\n\s*名\s*称\s*[:：]\s*([^\n\r，。；|]+)",
        ) or ""
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

        # 标记条款：按文件对 ★/※/▲ 的定义区分废标红线与扣分重要条款（宁多勿漏）
        a.star_disqualification_items, a.important_items, a.marker_legend = self._rule_marked_clauses(text)

        # 资质门槛：仅在原文出现关键词时记录
        quals = []
        for kw in ["CMMI", "ISO9001", "ISO27001", "ISO20000", "高新技术企业", "涉密", "ITSS", "CS3", "CS4", "CCRC", "信息系统建设和服务能力"]:
            if kw in text:
                quals.append(f"文件提及资质要求：{kw}（详见原文资质条款）")
        a.qualification_thresholds = quals

        return a

    @staticmethod
    def _rule_marked_clauses(text: str) -> Tuple[List[str], List[str], Dict[str, str]]:
        """按文件定义的标记含义拆分（废标红线, 重要扣分条款, 标记定义）"""
        semantics = marker_semantics(text)
        invalid = {m for m, (level, _) in semantics.items() if level == "invalid"}
        deduct = {m for m, (level, _) in semantics.items() if level == "deduct"}
        stars, important = [], []
        for line in text.split("\n"):
            line = line.strip()
            if not (4 < len(line) < 200) or is_marker_reference(line):
                continue
            marks = {c for c in line if c in CLAUSE_MARKERS}
            if marks & invalid:
                stars.append(line)
            elif marks & deduct:
                important.append(line)
        legend = {m: sentence for m, (_, sentence) in semantics.items() if sentence}
        return stars[:40], important[:60], legend


tender_analyzer = TenderAnalyzer()
