"""
技术偏离表引擎（强规则确定性抽取 + LLM 点对点响应）。

抽取（确定性，不用模型）：
1. 优先按招标文件章节树抽取：只取技术/服务需求篇（排除商务、须知、评标、合同、格式等），
   跳过项目概况/背景类小节；每个需求段落、每行需求表格为一条，保留章节路径溯源
2. 条款等级以文件自身的标记定义为准（tender_analyzer.marker_semantics）：
   redline 不满足即无效投标 / important 不满足按评分扣分 / normal；
   段落自身无标记时继承其所在小节标题上的标记（如「（3）★数据确权」下的各段）
3. 无章节树（粘贴文本、旧项目）时退回全文关键词扫描
4. 抽取不到就返回空，绝不用假条款填充

响应（LLM）：
- 每次调用批量响应 8 条，失败的条目逐条重试；只接受真实模型输出（get_mode()=="llm"）
- LLM 不可用时状态保持"待生成"——投标响应声明是法律承诺，不可编造
- 模型拟稿标记 response_source="ai"，提示人工核实后再提交
"""
import hashlib
import logging
import re
from typing import Dict, List, Optional, Tuple

from app.core.llm_client import llm_client
from app.models.schemas import DeviationItem, GlobalFacts
from app.services.parser.tender_analyzer import CLAUSE_MARKERS, is_marker_reference, marker_semantics
from app.services.rag.retriever import retrieval_service

logger = logging.getLogger("easywrite.deviation")

DEVIATION_PROMPT = """你是一名从业20年的资深投标技术专家，擅长编写点对点技术偏离表。
请针对招标文件中的技术指标，结合我方的全局事实和产品能力，给出严谨、明确、有依据的"点对点技术响应说明"。

【原则】：
1. 态度端正、庄重明确，明确标明"完全满足"或"正偏离"；只有真有把握优越于要求时才可声明正偏离；
2. 给出具体的实现技术手段（如：我司通过【产品名】的XXX组件，采用YYY机制实现，完全满足贵方要求）；
3. 严谨杜绝空话，篇幅在 40~120 字左右；
4. 若全局事实或资产中无相关能力支撑，宁可选"完全满足"并给出通用但具体的技术路径，也不得编造虚假资质。
"""

LEVEL_LABELS = {"redline": "废标红线", "important": "重要条款", "normal": "一般条款"}
LEVEL_ORDER = {"redline": 0, "important": 1, "normal": 2}
BATCH_SIZE = 8

REQUIREMENT_PART = re.compile(r"需求|要求")
EXCLUDED_PART = re.compile(r"商务|须知|评标|评审|评分|合同|格式|邀请|资格|报价")
# 动词「应」，排除「应用/应急/应答/应对/应收/应付」等名词用法
VERB_YING = r"应(?![用急答对收付标])"
BACKGROUND_SECTION = re.compile(r"概况|背景|简介|介绍|现状|一览表")
REQ_VERBS = re.compile(VERB_YING + r"|须|必须|要求|支持|具备|提供|实现|满足|不少于|不低于|不超过|不高于|≥|≤|确保|负责|完成|具有|包括|包含")
# 短标签里的"要求/包括"多是名词（「（二）服务标准及技术要求」），短行只认强约束动词
STRONG_VERBS = re.compile(VERB_YING + r"|须|必须|支持|具备|提供|实现|满足|不少于|不低于|不超过|不高于|≥|≤|确保")
TOTAL_ROW = re.compile(r"^(?:合计|总计|小计|总价)")
REQ_TABLE_HEADER = re.compile(r"需求|要求|内容|标准|参数|指标|功能|规格")
SCORING_HEADER = re.compile(r"分值|评分|得分")
NUMERIC_CELL = re.compile(r"^[\d.,，%％元万台套个项人天年月日/\s\-—]*$")


def _fingerprint(text: str) -> str:
    return hashlib.md5(re.sub(r"\s+", "", text).encode("utf-8")).hexdigest()[:16]


class _LevelResolver:
    """把文本中的标记映射为条款等级（标记含义来自招标文件自身的定义句）"""

    def __init__(self, legend_text: str):
        semantics = marker_semantics(legend_text)
        self.invalid = {m for m, (lvl, _) in semantics.items() if lvl == "invalid"}
        self.deduct = {m for m, (lvl, _) in semantics.items() if lvl == "deduct"}

    def level_of(self, text: str) -> Optional[str]:
        marks = {c for c in text if c in CLAUSE_MARKERS}
        if marks & self.invalid:
            return "redline"
        if marks & self.deduct:
            return "important"
        return None


class DeviationEngine:
    def __init__(self):
        self.llm = llm_client

    # ---------------- 确定性抽取：章节树 ----------------

    def extract_from_structure(self, sections: List[dict], legend_text: str = "") -> Tuple[List[DeviationItem], List[str]]:
        """
        从招标文件章节树抽取技术需求条款。
        返回（条款, 抽取范围的章节标题）；未定位到需求篇时返回 ([], [])，由调用方退回全文扫描。
        """
        parts = [s for s in sections
                 if REQUIREMENT_PART.search(s.get("title", "")) and not EXCLUDED_PART.search(s.get("title", ""))]
        if not parts:
            return [], []
        resolver = _LevelResolver(legend_text)
        items: List[DeviationItem] = []
        seen = set()

        def add(text: str, section_path: str, inherited: Optional[str]):
            text = text.strip()
            fp = _fingerprint(text)
            if fp in seen:
                return
            seen.add(fp)
            level = resolver.level_of(text) or inherited or "normal"
            items.append(DeviationItem(
                index=len(items) + 1, clause_title=text, is_star=level == "redline", level=level,
                section=section_path, response_status="待生成", response_detail="", source_fingerprint=fp,
            ))

        def walk(node: dict, path: List[str]):
            title = node.get("title", "")
            section_path = " > ".join(path + [title])
            inherited = resolver.level_of(title)
            if not (BACKGROUND_SECTION.search(title) and not inherited):
                # 带标记的短标签（「★1.1.6数据质量提升」）把等级与标签名带给其后的需求段落，直到下一个标签
                label, label_level, label_used = "", None, True
                for para in (node.get("content") or "").split("\n\n"):
                    para = para.strip()
                    if self._is_label(para):
                        if label and not label_used:
                            add(label, section_path, label_level)
                        label, label_level = para, resolver.level_of(para)
                        label_used = label_level is None  # 无标记的结构性标签不单独成条
                        continue
                    if self._is_requirement(para, resolver):
                        if label_level:
                            add(f"{label}：{para}", section_path, label_level)
                            label_used = True
                        else:
                            add(para, section_path, inherited)
                if label and not label_used:
                    add(label, section_path, label_level)
                for table in node.get("tables") or []:
                    for row_text in self._requirement_rows(table):
                        add(row_text, section_path, inherited)
            for child in node.get("subsections") or []:
                walk(child, path + [title])

        for part in parts:
            walk(part, [])
        items.sort(key=lambda it: (LEVEL_ORDER.get(it.level, 2), it.index))
        for i, it in enumerate(items, 1):
            it.index = i
        return items, [p["title"] for p in parts]

    @staticmethod
    def _is_label(para: str) -> bool:
        """段落形式的小标题：短、无强约束动词、不以句读结尾（「（二）服务标准及技术要求」「★1.1.6数据质量提升」）"""
        p = para.strip()
        return 0 < len(p) < 30 and not STRONG_VERBS.search(p) and p[-1] not in "。；;！!？?：:，,"

    @staticmethod
    def _is_requirement(para: str, resolver: _LevelResolver) -> bool:
        p = para.strip()
        if len(p) < 8 or is_marker_reference(p) or TOTAL_ROW.match(p):
            return False
        if resolver.level_of(p):
            return True
        return bool(REQ_VERBS.search(p))

    @staticmethod
    def _requirement_rows(table: List[List[str]]) -> List[str]:
        """需求类表格逐行成条：「需求分类：具体需求」；评分表与纯数量/金额列不纳入"""
        if len(table) < 2:
            return []
        header = " ".join(table[0])
        if SCORING_HEADER.search(header) or not REQ_TABLE_HEADER.search(header):
            return []
        rows = []
        for row in table[1:]:
            cells = [c.strip() for c in row if c and c.strip()]
            cells = [c for i, c in enumerate(cells) if not (i == 0 and re.fullmatch(r"\d{1,3}", c))]
            cells = [c for c in cells if not NUMERIC_CELL.match(c)]
            cells = list(dict.fromkeys(cells))  # 纵向合并造成的重复单元格
            text = "：".join(cells)
            if len(text) >= 8 and not TOTAL_ROW.match(text):
                rows.append(text)
        return rows

    # ---------------- 确定性抽取：全文扫描（无章节树时的兜底） ----------------

    def extract_requirements(self, tender_text: str, max_items: int = 60) -> List[DeviationItem]:
        """从招标文件纯文本中确定性提取条目化技术要求清单（无 LLM，纯规则）"""
        resolver = _LevelResolver(tender_text or "")
        items: List[DeviationItem] = []
        seen = set()
        for line in (l.strip() for l in (tender_text or "").split("\n")):
            if not line or is_marker_reference(line):
                continue
            level = resolver.level_of(line)
            is_req_line = any(
                kw in line for kw in ["必须", "应当", "支持", "具备", "提供", "采用", "配置", "实现", "符合"]
            ) and 6 <= len(line) <= 300
            if not (level or is_req_line):
                continue
            clean_title = re.sub(r"^[0-9]+[、.．\s]+", "", line)
            fp = _fingerprint(clean_title)
            if fp in seen:
                continue
            seen.add(fp)
            level = level or "normal"
            items.append(DeviationItem(
                index=len(items) + 1, clause_title=clean_title, is_star=level == "redline", level=level,
                response_status="待生成", response_detail="", source_fingerprint=fp,
            ))
            if len(items) >= max_items:
                break
        items.sort(key=lambda it: (LEVEL_ORDER.get(it.level, 2), it.index))
        for i, item in enumerate(items, 1):
            item.index = i
        return items

    def coverage_report(self, items: List[DeviationItem], tender_text: str, scope: Optional[List[str]] = None) -> dict:
        """覆盖率核算：文件中带红线标记的行 vs 抽取到的红线条款"""
        resolver = _LevelResolver(tender_text or "")
        marked_lines = [
            l for l in (tender_text or "").split("\n")
            if l.strip() and not is_marker_reference(l) and resolver.level_of(l) == "redline"
        ]
        covered = sum(1 for it in items if it.level == "redline")
        note = ""
        if scope is not None and not scope:
            note = "未定位到技术/服务需求章节，已退回全文关键词扫描，条款可能混入非技术内容，请人工核对"
        elif not scope and len(marked_lines) > covered:
            note = "红线标记条款存在未覆盖行，建议人工核对招标文件红线条款"
        return {
            "extracted_total": len(items),
            "redline_count": covered,
            "important_count": sum(1 for it in items if it.level == "important"),
            "star_in_tender": len(marked_lines),
            "star_covered": covered,
            "star_uncovered": max(0, len(marked_lines) - covered),
            "scope": scope or [],
            "note": note,
        }

    # ---------------- LLM 点对点响应 ----------------

    @staticmethod
    def _facts_text(facts: GlobalFacts) -> str:
        if facts.filled_count() > 0:
            return (f"投标企业【{facts.company_name}】，核心产品【{facts.core_product_name}】，"
                    f"技术栈【{facts.architecture_stack}】，数据库【{facts.database_selection}】")
        return "（投标主体事实未配置——请给出通用但具体的技术路径表述，不得提及具体公司名）"

    @staticmethod
    def _item_context(item: DeviationItem) -> Tuple[str, str]:
        retrieval = retrieval_service.retrieve(query=item.clause_title, top_k=2, rerank=False)
        ref_context = retrieval["refs"][0]["content"][:250] if retrieval["refs"] else ""
        from app.services.assets.asset_manager import asset_manager
        comp_context = asset_manager.match_assets_for_section(item.clause_title, []).get("context_text", "")[:250]
        return ref_context, comp_context

    @staticmethod
    def _apply(item: DeviationItem, status: str, detail: str):
        item.response_status = status if status in ("完全满足", "正偏离") else "完全满足"
        item.response_detail = detail.strip()[:300]
        item.response_source = "ai"

    def generate_response_for_item(self, item: DeviationItem, facts: GlobalFacts) -> DeviationItem:
        """针对单个指标生成点对点应答（LLM 不可用时留空待生成，不编造）"""
        if self.llm.is_configured:
            ref_context, comp_context = self._item_context(item)
            user_prompt = (
                f"【招标文件要求】：{item.clause_title}\n"
                f"【条款等级】：{LEVEL_LABELS.get(item.level, '一般条款')}"
                f"{'（绝不可产生负偏离）' if item.level == 'redline' else ''}\n"
                f"【我司全局事实】：{self._facts_text(facts)}\n"
                f"【企业资料（用户录入）】：{comp_context or '无匹配资料'}\n"
                f"【历史标书参考】：{ref_context or '无'}\n\n"
                f"请以 JSON 格式输出：\n"
                f'{{"response_status": "完全满足" 或 "正偏离", "response_detail": "点对点具体技术佐证阐述（50~120字）"}}'
            )
            data = self.llm.chat_completion_structured(
                system_prompt=DEVIATION_PROMPT, user_prompt=user_prompt, temperature=0.2, purpose="deviation_response"
            )
            if isinstance(data, dict) and data.get("response_detail") and self.llm.get_mode() == "llm":
                self._apply(item, str(data.get("response_status", "完全满足")), str(data["response_detail"]))
                return item
            # 普通文本兜底（仅接受真实模型输出，模拟器内容不得冒充响应声明）
            res = self.llm.chat_completion(
                system_prompt=DEVIATION_PROMPT, user_prompt=user_prompt, temperature=0.2, purpose="deviation_response"
            )
            if res and len(res) > 10 and self.llm.get_mode() == "llm":
                self._apply(item, "正偏离" if ("优于" in res or "正偏离" in res) else "完全满足", res)
                return item

        # LLM 不可用：留空待生成（不编造响应承诺）
        item.response_status = "待生成"
        item.response_detail = ""
        return item

    def _generate_batch(self, batch: List[DeviationItem], facts: GlobalFacts) -> List[DeviationItem]:
        """一次调用响应多条；返回未能成功响应的条目（由调用方逐条重试）"""
        blocks = []
        for i, item in enumerate(batch, 1):
            ref_context, comp_context = self._item_context(item)
            blocks.append(
                f"[{i}] 等级：{LEVEL_LABELS.get(item.level, '一般条款')}"
                f"{'（绝不可负偏离）' if item.level == 'redline' else ''}\n"
                f"    招标要求：{item.clause_title[:600]}\n"
                f"    可用资产：{comp_context or '无'}\n    历史参考：{ref_context or '无'}"
            )
        user_prompt = (
            f"【我司全局事实】：{self._facts_text(facts)}\n\n【逐条响应以下 {len(batch)} 项招标要求】：\n"
            + "\n".join(blocks)
            + '\n\n请以 JSON 格式输出：{"responses": [{"no": 1, "response_status": "完全满足" 或 "正偏离", '
              '"response_detail": "点对点具体技术佐证阐述（50~120字）"}]}，no 与上面的编号一一对应，不得遗漏。'
        )
        data = self.llm.chat_completion_structured(
            system_prompt=DEVIATION_PROMPT, user_prompt=user_prompt, temperature=0.2, purpose="deviation_response"
        )
        if not (isinstance(data, dict) and isinstance(data.get("responses"), list) and self.llm.get_mode() == "llm"):
            return batch
        answered: Dict[int, dict] = {}
        for r in data["responses"]:
            if isinstance(r, dict) and str(r.get("no", "")).isdigit() and r.get("response_detail"):
                answered[int(r["no"])] = r
        failed = []
        for i, item in enumerate(batch, 1):
            r = answered.get(i)
            if r:
                self._apply(item, str(r.get("response_status", "完全满足")), str(r["response_detail"]))
            else:
                failed.append(item)
        return failed

    def batch_generate_responses(
        self, items: List[DeviationItem], facts: GlobalFacts, progress=None
    ) -> List[DeviationItem]:
        """批量生成技术偏离响应：每次调用 8 条，失败条目逐条重试（支持进度上报与取消）"""
        total = len(items)
        if not self.llm.is_configured:
            for item in items:
                item.response_status, item.response_detail = "待生成", ""
            return items
        done = 0
        for start in range(0, total, BATCH_SIZE):
            if progress and progress.cancelled():
                break
            batch = items[start:start + BATCH_SIZE]
            for item in self._generate_batch(batch, facts):
                if progress and progress.cancelled():
                    break
                self.generate_response_for_item(item, facts)
            done += len(batch)
            if progress:
                progress.report(int(done / max(total, 1) * 100), f"点对点响应 {done}/{total}")
        return items

    @staticmethod
    def to_markdown_table(items: List[DeviationItem]) -> str:
        """将偏离表项转换为标准 Markdown 表格（总结语按实际状态生成，不无条件声明无偏离）"""
        lines = [
            "| 序号 | 招标文件技术规格要求 | 条款属性 | 投标响应状态 | 点对点详细技术方案与响应说明 |",
            "| :---: | :--- | :---: | :---: | :--- |",
        ]
        for it in items:
            level_label = LEVEL_LABELS.get(it.level, "一般条款")
            status_label = f"**{it.response_status}**"
            clean_clause = it.clause_title.replace("|", " ").replace("\n", " ")
            clean_detail = it.response_detail.replace("|", " ").replace("\n", " ")
            lines.append(f"| {it.index} | {clean_clause} | {level_label} | {status_label} | {clean_detail} |")

        has_negative = any(it.response_status == "负偏离" for it in items)
        has_pending = any(it.response_status == "待生成" for it in items)
        if has_negative:
            conclusion = "\n\n**注意：存在负偏离条款，请立即整改后再行导出！**"
        elif has_pending:
            conclusion = "\n\n**注意：部分条款响应尚未生成，请补充后再导出。**"
        else:
            conclusion = (
                "\n\n**技术偏离说明总结**：\n"
                "我方对招标文件列出的全部技术指标进行了逐项点对点核对，"
                "均达到完全满足或正偏离标准，不存在负偏离条款与保留项。"
            )
        return "\n".join(lines) + conclusion


deviation_engine = DeviationEngine()
