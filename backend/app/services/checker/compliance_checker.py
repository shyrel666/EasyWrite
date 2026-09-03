"""
废标项与合规核查引擎（LLM 多轮证据核查制，规则快扫保留为前置标记）。

对比旧版的核心升级：
- 旧版是纯子串匹配（"系统"二字就能满足一条★条款），且存在未使用的死代码 prompt
- 新版借鉴 OpenBidKit rejectionCheckTask 的多轮制：
  Round 1 范围界定 → Round 2 逐条款证据核查（含证据摘录定位）→ 汇总 JSON 定稿
- 关键词快扫保留：负偏离敏感词、企业名一致性仍由规则前置捕获（快、确定性）
- LLM 不可用时明确 mode=rules，报告注明"仅关键词快扫，覆盖度有限"
"""
import logging
import re
from typing import List, Dict, Optional

from app.core.llm_client import llm_client
from app.models.schemas import OutlineNode, GlobalFacts, ComplianceCheckReport

logger = logging.getLogger("easywrite.compliance")

NEGATIVE_KEYWORDS = [
    "不满足", "无法满足", "暂不支持", "无法提供", "存在负偏离",
    "不承诺", "待后续协商", "无法保证", "部分满足",
]

# 单轮核查的标书正文字符上限（超长分批，防止上下文溢出）
CHECK_BATCH_CHARS = 24000

_ROUND1_SYSTEM = """你是废标审查范围界定专家。给定★号不可偏离条款清单与标书章节目录，
判断每条条款属于：
- "text_check"：可通过标书正文电子化核查（技术参数、功能承诺、资质响应等）
- "paper_only"：依赖纸质/线下材料（签字、盖章、原件、保函等），电子正文无法核查
只输出 JSON：{"classification": [{"item": "条款原文", "type": "text_check|paper_only"}]}"""

_ROUND2_SYSTEM = """你是资深政府采购废标审查专家。逐条核查投标方案正文是否实质性响应了★号不可偏离条款。

【判定标准】：
- satisfied：正文存在明确、具体、可核验的响应论述（有具体技术参数/承诺/方案，而非空泛表态）
- unverified：正文完全未提及或只有含糊其辞的表态
- negative：正文明确表达了不满足/负偏离/需协商
- paper_only：该条款依赖线下材料，电子正文无法判定

【证据纪律】：satisfied 判定必须给出正文中支撑该结论的证据摘录（从原文复制，不超过60字）；
摘录必须真实存在于正文中，不得杜撰。

只输出 JSON：
{"results": [{"item": "条款原文", "verdict": "satisfied|unverified|negative|paper_only",
  "evidence": "证据摘录或空", "suggestion": "不通过时的整改建议（不超过50字）"}]}"""


class ComplianceChecker:
    def __init__(self):
        self.llm = llm_client

    @staticmethod
    def _collect_all_content(outline: List[OutlineNode]) -> List[Dict[str, str]]:
        sections = []

        def traverse(nodes):
            for n in nodes:
                if n.content and len(n.content.strip()) > 20:
                    sections.append({"title": n.path or n.title, "content": n.content})
                traverse(n.children)

        traverse(outline)
        return sections

    @staticmethod
    def _sections_to_text(sections: List[Dict[str, str]]) -> str:
        return "\n\n".join(f"【{s['title']}】\n{s['content']}" for s in sections)

    def check_compliance(
        self,
        star_items: List[str],
        outline: List[OutlineNode],
        facts: GlobalFacts,
        progress=None,
    ) -> ComplianceCheckReport:
        sections = self._collect_all_content(outline)
        full_text = self._sections_to_text(sections)

        risk_items: List[Dict[str, str]] = []
        warnings: List[str] = []

        # ---- 前置规则快扫：负偏离敏感词 ----
        for kw in NEGATIVE_KEYWORDS:
            pos = full_text.find(kw)
            if pos >= 0:
                snippet = full_text[max(0, pos - 20) : pos + 30].replace("\n", " ")
                risk_items.append({
                    "item": f"检测到负偏离敏感词「{kw}」",
                    "reason": f"上下文：'…{snippet}…'，极易被评标专家判定为实质性负偏离而废标",
                    "severity": "HIGH",
                    "suggestion": "改写为正面响应表述或删除该表述",
                })

        # ---- 前置规则快扫：企业名一致性 ----
        if facts.company_name and facts.company_name not in full_text:
            warnings.append(f"标书正文未显式提及全局事实设定的企业全称【{facts.company_name}】，建议核对封面及正文落款。")

        star_items = [s for s in (star_items or []) if s and s.strip()]
        if not star_items:
            return ComplianceCheckReport(
                passed=len(risk_items) == 0,
                total_star_items=0,
                satisfied_star_items=0,
                risk_items=risk_items,
                warnings=warnings,
                summary="未提供★号条款清单（请先在18项拆标中提取★条款），本次仅执行了负偏离敏感词快扫。",
                mode="rules",
                checked_sections=len(sections),
            )

        # ---- LLM 多轮核查 ----
        if self.llm.is_configured and sections:
            llm_results = self._llm_check(star_items, sections, progress)
            if llm_results is not None:
                satisfied = 0
                for r in llm_results:
                    verdict = r.get("verdict", "unverified")
                    if verdict == "satisfied":
                        satisfied += 1
                    elif verdict == "negative":
                        risk_items.append({
                            "item": r.get("item", ""),
                            "reason": f"正文存在负偏离表述。证据：{r.get('evidence', '')}",
                            "severity": "HIGH",
                            "suggestion": r.get("suggestion", "立即整改为完全响应表述"),
                        })
                    elif verdict == "unverified":
                        risk_items.append({
                            "item": r.get("item", ""),
                            "reason": "正文中未找到针对该★号条款的明确、可核验的响应论述（含糊表态不足以满足实质性响应要求）",
                            "severity": "HIGH",
                            "suggestion": r.get("suggestion", "补充具体技术参数与承诺表述"),
                        })
                    elif verdict == "paper_only":
                        warnings.append(f"条款依赖线下材料，正文无法判定：{r.get('item', '')[:60]}")
                passed = len(risk_items) == 0 and satisfied >= len(star_items)
                summary = (
                    f"（LLM 证据核查）共核查 {len(star_items)} 项★号条款：明确响应 {satisfied} 项，"
                    f"高危风险 {len(risk_items)} 项，轻度提醒 {len(warnings)} 项。"
                    + ("结论：合规自检通过。" if passed else "结论：存在高危废标风险，必须在交卷前整改！")
                )
                return ComplianceCheckReport(
                    passed=passed,
                    total_star_items=len(star_items),
                    satisfied_star_items=satisfied,
                    risk_items=risk_items,
                    warnings=warnings,
                    summary=summary,
                    mode="llm",
                    checked_sections=len(sections),
                )

        # ---- 规则兜底：显式关键词覆盖率（注明局限） ----
        satisfied = 0
        for star in star_items:
            clean = star.replace("★", "").replace("▲", "").strip()
            # 提取长关键词（≥3字的连续汉字段），命中≥2个或命中任一≥5字关键词才算覆盖
            kws = [w for w in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{3,}", clean)
                   if w not in ("投标人", "所投系统", "必须", "应当", "本项目")]
            hits = [w for w in kws if w in full_text]
            if len(hits) >= 2 or any(len(h) >= 5 for h in hits):
                satisfied += 1
            else:
                risk_items.append({
                    "item": star,
                    "reason": "（关键词快扫模式）正文中未检测到该★号条款的核心关键词组合",
                    "severity": "MEDIUM",
                    "suggestion": "配置大模型后执行 LLM 证据级核查以获得准确结论",
                })
        passed = len(risk_items) == 0 and satisfied >= len(star_items)
        return ComplianceCheckReport(
            passed=passed,
            total_star_items=len(star_items),
            satisfied_star_items=satisfied,
            risk_items=risk_items,
            warnings=warnings,
            summary=(
                f"（关键词快扫模式，精度有限——未配置大模型或无正文内容）共核查 {len(star_items)} 项★号条款，"
                f"关键词覆盖 {satisfied} 项，风险 {len(risk_items)} 项。建议配置 LLM 后重新执行证据级核查。"
            ),
            mode="rules",
            checked_sections=len(sections),
        )

    def _llm_check(self, star_items, sections, progress=None) -> Optional[List[Dict]]:
        """Round 1 范围界定 → Round 2 分批逐条款证据核查"""
        toc = "\n".join(s["title"] for s in sections)

        # Round 1：条款分类（paper_only 剔除正文核查）
        text_items = star_items
        r1 = self.llm.chat_completion_structured(
            _ROUND1_SYSTEM,
            f"★号条款清单：\n" + "\n".join(f"- {s}" for s in star_items) + f"\n\n标书章节目录：\n{toc}",
            temperature=0.1,
        )
        paper_only = set()
        if isinstance(r1, dict) and isinstance(r1.get("classification"), list):
            text_items = []
            for c in r1["classification"]:
                if isinstance(c, dict) and c.get("type") == "paper_only":
                    paper_only.add(str(c.get("item", "")))
            text_items = [s for s in star_items if s not in paper_only]

        # Round 2：正文分批核查
        all_results: List[Dict] = []
        for s in star_items:
            if s in paper_only:
                all_results.append({"item": s, "verdict": "paper_only", "evidence": "", "suggestion": ""})

        body = self._sections_to_text(sections)
        batches = [body[i : i + CHECK_BATCH_CHARS] for i in range(0, len(body), CHECK_BATCH_CHARS)] or [body]
        pending = list(text_items)

        for bi, batch in enumerate(batches):
            if not pending:
                break
            user_prompt = (
                f"★号不可偏离条款（逐条核查）：\n" + "\n".join(f"- {s}" for s in pending)
                + f"\n\n投标方案正文（第 {bi + 1}/{len(batches)} 批）：\n{batch}"
            )
            r2 = self.llm.chat_completion_structured(_ROUND2_SYSTEM, user_prompt, temperature=0.1)
            if not (isinstance(r2, dict) and isinstance(r2.get("results"), list)):
                logger.warning("合规核查 Round 2 第 %d 批解析失败", bi + 1)
                continue
            verdicts = {}
            for item in r2["results"]:
                if isinstance(item, dict) and item.get("item"):
                    verdicts[str(item["item"])] = item
            next_pending = []
            for s in pending:
                hit = verdicts.get(s) or self._fuzzy_find_verdict(s, verdicts)
                if hit:
                    all_results.append({
                        "item": s,
                        "verdict": str(hit.get("verdict", "unverified")),
                        "evidence": str(hit.get("evidence", ""))[:120],
                        "suggestion": str(hit.get("suggestion", ""))[:80],
                    })
                else:
                    next_pending.append(s)  # 该批未覆盖，留待下一批
            pending = next_pending
            if progress:
                progress.report(int((bi + 1) / len(batches) * 100), f"证据核查批次 {bi + 1}/{len(batches)}")

        # 所有批次都没给出结论的条款 → unverified
        for s in pending:
            all_results.append({"item": s, "verdict": "unverified", "evidence": "", "suggestion": "正文未见响应，请补充论述"})

        return all_results if all_results else None

    @staticmethod
    def _fuzzy_find_verdict(star: str, verdicts: Dict[str, Dict]) -> Optional[Dict]:
        """LLM 回显条款原文可能有细微差异，做包含式匹配"""
        key = re.sub(r"\s+", "", star)[:40]
        for v_item, v in verdicts.items():
            if key and (key in re.sub(r"\s+", "", v_item) or re.sub(r"\s+", "", v_item)[:40] in key):
                return v
        return None


compliance_checker = ComplianceChecker()
