"""
技术偏离表引擎（强规则确定性抽取 + LLM 点对点响应）。

抽取（确定性，不用模型）：
1. 优先按招标文件章节树抽取：只取技术/服务需求篇（排除商务、须知、评标、合同、格式等），
   跳过项目概况/背景类小节；每个需求段落、每行需求表格为一条，保留章节路径溯源
2. 条款等级以文件自身的标记定义为准（tender_analyzer.marker_semantics）：
   redline 不满足即无效投标 / important 不满足按评分扣分 / normal；
   段落自身无标记时继承其所在小节标题上的标记（如「（3）★数据确权」下的各段）
3. 多分包项目只保留投标分包与各包通用的条款，其他分包的条款排除后计数（分包识别规则见 extract_from_structure）
4. 无章节树（粘贴文本、旧项目）时退回全文关键词扫描（不分包）
5. 抽取不到就返回空，绝不用假条款填充

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
from app.services.parser.scoring_extractor import PACKAGE_RE, package_labels
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
# 分包标签段落的序号前缀：「1.A包：…」「（3）C包：…」
PACKAGE_LEAD = re.compile(r"^(?:[（(][一二三四五六七八九十\d]{1,3}[)）]|[一二三四五六七八九十\d]{1,3}[、.．)）])?\s*")


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

    def extract_from_structure(
        self, sections: List[dict], legend_text: str = "", target_package: str = ""
    ) -> Tuple[List[DeviationItem], List[str], dict]:
        """
        从招标文件章节树抽取技术需求条款。
        返回（条款, 抽取范围的章节标题, 分包情况）；未定位到需求篇时条款与范围为空，由调用方退回全文扫描。

        分包（多分包项目的需求篇按包分写）：
        - 标题里的分包（「二、包一招标项目服务需求」「（一）包1服务范围、要求及标准」）管到该章节结束；
        - 分包标签段落（「第2包」「包2：等保及密评服务」「1.A包：…运行维护」）管到下一个标签或所在章节结束；
          它是章节最后一块（其后没有表格和下级章节）时是后续同级章节的引导语，管到上级章节结束；
        - 标题或标签写了几个分包（「四、商务要求（A包、B包）」）为各包通用；需求表格首列写了分包的行归该包。
        target_package 是需求篇里出现的分包时，只保留该包与通用条款，其余排除并计数；
        对应不上（或没有投标分包）时不过滤，由分包情况的 note 提示人工核对。
        """
        packages = {"target": target_package, "found": [], "filtered": False,
                    "excluded": 0, "excluded_by_package": {}, "note": ""}
        parts = [s for s in sections
                 if REQUIREMENT_PART.search(s.get("title", "")) and not EXCLUDED_PART.search(s.get("title", ""))]
        if not parts:
            return [], [], packages
        resolver = _LevelResolver(legend_text)
        items: List[DeviationItem] = []
        seen = set()
        found: List[str] = []
        # 当前分包（"" = 各包通用）；until：分包标签段落的作用范围，退出该深度的章节时失效
        state = {"pkg": "", "until": None}

        def note_packages(labels: List[str]):
            found.extend(p for p in labels if p not in found)

        def add(text: str, section_path: str, inherited: Optional[str], package: str):
            text = text.strip()
            fp = _fingerprint(text)
            if fp in seen:
                return
            seen.add(fp)
            level = resolver.level_of(text) or inherited or "normal"
            items.append(DeviationItem(
                index=len(items) + 1, clause_title=text, is_star=level == "redline", level=level,
                section=section_path, package=package, response_status="待生成", response_detail="",
                source_fingerprint=fp,
            ))

        def walk(node: dict, path: List[str], depth: int, base: str):
            title = node.get("title", "")
            section_path = " > ".join(path + [title])
            inherited = resolver.level_of(title)
            saved = dict(state)
            title_packages = package_labels(title)
            if title_packages:
                note_packages(title_packages)
                base = title_packages[0] if len(title_packages) == 1 else ""
                state.update(pkg=base, until=None)
            subsections = node.get("subsections") or []
            blocks = self._blocks(node)
            skip = BACKGROUND_SECTION.search(title) and not inherited
            # 带标记的短标签（「★1.1.6数据质量提升」）把等级与标签名带给其后的需求段落，直到下一个标签
            label, label_level, label_used = "", None, True
            for i, (kind, value) in enumerate(blocks):
                if kind == "table":
                    if not skip:
                        for row_package, row_text in self._requirement_rows(value):
                            if row_package is not None:
                                note_packages([row_package] if row_package else [])
                            add(row_text, section_path, inherited, state["pkg"] if row_package is None else row_package)
                    continue
                marker = self._package_marker(value)
                if marker is not None:
                    if label and not label_used and not skip:
                        add(label, section_path, label_level, state["pkg"])
                    label, label_level, label_used = "", None, True
                    note_packages(package_labels(value))
                    last = i == len(blocks) - 1 and not subsections
                    state.update(pkg=marker, until=depth - 1 if last else depth)
                    continue
                if skip:
                    continue
                if self._is_label(value):
                    if label and not label_used:
                        add(label, section_path, label_level, state["pkg"])
                    label, label_level = value, resolver.level_of(value)
                    label_used = label_level is None  # 无标记的结构性标签不单独成条
                    continue
                if self._is_requirement(value, resolver):
                    if label_level:
                        add(f"{label}：{value}", section_path, label_level, state["pkg"])
                        label_used = True
                    else:
                        add(value, section_path, inherited, state["pkg"])
            if label and not label_used and not skip:
                add(label, section_path, label_level, state["pkg"])
            for child in subsections:
                walk(child, path + [title], depth + 1, base)
            if title_packages:
                state.update(saved)
            elif state["until"] is not None and state["until"] >= depth:
                state.update(pkg=base, until=None)

        for part in parts:
            state.update(pkg="", until=None)
            walk(part, [], 0, "")

        packages["found"] = found
        if target_package and target_package in found:
            kept, excluded = [], {}
            for it in items:
                if it.package and it.package != target_package:
                    excluded[it.package] = excluded.get(it.package, 0) + 1
                else:
                    kept.append(it)
            items = kept
            packages.update(filtered=True, excluded=sum(excluded.values()), excluded_by_package=excluded)
        elif len(found) > 1 or (target_package and found):
            names = "、".join(found)
            packages["note"] = (
                f"需求篇按分包分写（{names}），与投标分包「{target_package}」对应不上，未按分包过滤，请人工删除其他分包的条款"
                if target_package else
                f"需求篇按分包分写（{names}），但评分表未区分分包、无法确定投标分包，未按分包过滤，请人工删除其他分包的条款"
            )
        items.sort(key=lambda it: (LEVEL_ORDER.get(it.level, 2), it.index))
        for i, it in enumerate(items, 1):
            it.index = i
        return items, [p["title"] for p in parts], packages

    @staticmethod
    def _blocks(node: dict) -> List[Tuple[str, object]]:
        """章节的段落与表格按文档顺序排列；旧存档没有 table_anchors 时表格排在段落之后"""
        paras = [p.strip() for p in (node.get("content") or "").split("\n\n") if p.strip()]
        tables = node.get("tables") or []
        anchors = node.get("table_anchors") or []
        if len(anchors) != len(tables):
            anchors = [len(paras)] * len(tables)
        blocks: List[Tuple[str, object]] = []
        t = 0
        for i, para in enumerate(paras):
            while t < len(tables) and anchors[t] <= i:
                blocks.append(("table", tables[t]))
                t += 1
            blocks.append(("para", para))
        blocks.extend(("table", table) for table in tables[t:])
        return blocks

    @staticmethod
    def _package_marker(text: str) -> Optional[str]:
        """
        分包标签：以分包开头的短行（「第2包」「A包:」「包2：等保及密评服务」「1.A包：…运行维护」）。
        返回该分包；写了几个分包的（「A包、B包通用要求」）返回 ""（各包通用）；不是标签返回 None。
        句子（含句读，如「A包主要内容：……。」）不是标签。
        """
        p = (text or "").strip()
        if not p or len(p) > 60 or re.search(r"[。；;]", p):
            return None
        m = PACKAGE_RE.match(p, PACKAGE_LEAD.match(p).end())
        # 标签后须是分隔符或行尾：「包1年免费维保」的「包1」不是分包
        if not m or (m.end() < len(p) and p[m.end()] not in "：:（( 　、，,-—"):
            return None
        labels = package_labels(p)
        return labels[0] if len(labels) == 1 else ""

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

    @classmethod
    def _requirement_rows(cls, table: List[List[str]]) -> List[Tuple[Optional[str], str]]:
        """
        需求类表格逐行成条：（首列写的分包或 None, 「需求分类：具体需求」）；评分表与纯数量/金额列不纳入
        """
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
                rows.append((cls._package_marker(cells[0]), text))
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

    def coverage_report(self, items: List[DeviationItem], tender_text: str, scope: Optional[List[str]] = None,
                        packages: Optional[dict] = None) -> dict:
        """覆盖率核算：文件中带红线标记的行 vs 抽取到的红线条款；packages 为 extract_from_structure 的分包情况"""
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
        if packages and packages.get("note"):
            note = "；".join(n for n in (note, packages["note"]) if n)
        return {
            "extracted_total": len(items),
            "redline_count": covered,
            "important_count": sum(1 for it in items if it.level == "important"),
            "star_in_tender": len(marked_lines),
            "star_covered": covered,
            "star_uncovered": max(0, len(marked_lines) - covered),
            "scope": scope or [],
            "package": packages or {},
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
    def _item_context(item: DeviationItem, exclude=None) -> Tuple[str, str]:
        """exclude：资料排除规则（evidence.generation_filter），证书过期、主体不符的资料不进入应答提示词"""
        retrieval = retrieval_service.retrieve(query=item.clause_title, top_k=2, rerank=False)
        ref_context = retrieval["refs"][0]["content"][:250] if retrieval["refs"] else ""
        from app.services.assets.asset_manager import asset_manager
        comp_context = asset_manager.match_assets_for_section(
            item.clause_title, [], exclude=exclude).get("context_text", "")[:250]
        return ref_context, comp_context

    @staticmethod
    def _apply(item: DeviationItem, status: str, detail: str):
        item.response_status = status if status in ("完全满足", "正偏离") else "完全满足"
        item.response_detail = detail.strip()[:300]
        item.response_source = "ai"

    def generate_response_for_item(self, item: DeviationItem, facts: GlobalFacts, exclude=None) -> DeviationItem:
        """针对单个指标生成点对点应答（LLM 不可用时留空待生成，不编造）"""
        if self.llm.is_configured:
            ref_context, comp_context = self._item_context(item, exclude)
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

    def _generate_batch(self, batch: List[DeviationItem], facts: GlobalFacts, exclude=None) -> List[DeviationItem]:
        """一次调用响应多条；返回未能成功响应的条目（由调用方逐条重试）"""
        blocks = []
        for i, item in enumerate(batch, 1):
            ref_context, comp_context = self._item_context(item, exclude)
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
        self, items: List[DeviationItem], facts: GlobalFacts, progress=None, exclude=None
    ) -> List[DeviationItem]:
        """批量生成技术偏离响应：每次调用 8 条，失败条目逐条重试（支持进度上报与取消）；exclude 见 _item_context"""
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
            for item in self._generate_batch(batch, facts, exclude):
                if progress and progress.cancelled():
                    break
                self.generate_response_for_item(item, facts, exclude)
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
