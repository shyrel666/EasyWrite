"""
技术偏离表引擎（强规则确定性抽取 + LLM 点对点响应）。

核心修复（对照旧版）：
1. 删除离线兜底的 5 条编造指标——抽取不到就返回空，绝不用假条款填充
2. 删除离线模板自动写"完全满足"的伪造响应——LLM 不可用时响应留空待生成，
   状态标记"待生成"，由前端明确提示（投标响应声明是法律承诺，不可编造）
3. 抽取增加 source_fingerprint 溯源指纹（确定性抽取，便于覆盖率核算）
4. 知识召回接入 retrieval_service 混合检索
"""
import hashlib
import logging
import re
from typing import List, Optional

from app.core.llm_client import llm_client
from app.models.schemas import DeviationItem, GlobalFacts
from app.services.rag.retriever import retrieval_service

logger = logging.getLogger("easywrite.deviation")

DEVIATION_PROMPT = """你是一名从业20年的资深投标技术专家，擅长编写点对点技术偏离表。
请针对招标文件中的这一项技术指标，结合我方的全局事实和产品能力，给出严谨、明确、有依据的"点对点技术响应说明"。

【原则】：
1. 态度端正、庄重明确，明确标明"完全满足"或"正偏离"；只有真有把握优越于要求时才可声明正偏离；
2. 给出具体的实现技术手段（如：我司通过【产品名】的XXX组件，采用YYY机制实现，完全满足贵方要求）；
3. 严谨杜绝空话，篇幅在 40~120 字左右；
4. 若全局事实或资产中无相关能力支撑，宁可选"完全满足"并给出通用但具体的技术路径，也不得编造虚假资质。
"""


class DeviationEngine:
    def __init__(self):
        self.llm = llm_client

    # ---------------- 确定性抽取 ----------------

    def extract_requirements(self, tender_text: str, max_items: int = 40) -> List[DeviationItem]:
        """从招标文件文本中确定性提取条目化技术要求清单（无 LLM，纯规则）"""
        items: List[DeviationItem] = []
        seen_fingerprints = set()
        counter = 1

        candidate_clauses = []
        raw_lines = [l.strip() for l in (tender_text or "").split("\n") if l.strip()]
        for line in raw_lines:
            line_has_star = "★" in line or line.startswith("▲")
            if len(line) > 25 and ("。" in line or "；" in line):
                sub_parts = [p.strip() for p in re.split(r"[。；;]+", line) if p.strip()]
                for sp in sub_parts:
                    if line_has_star and not ("★" in sp or sp.startswith("▲")):
                        sp = "★ " + sp
                    candidate_clauses.append(sp)
            else:
                candidate_clauses.append(line)

        for line in candidate_clauses:
            is_star = "★" in line or line.startswith("▲")
            is_req_line = any(
                kw in line for kw in ["必须", "应当", "支持", "具备", "提供", "采用", "配置", "实现", "符合"]
            ) and 6 <= len(line) <= 300

            if is_star or is_req_line:
                clean_title = re.sub(r"^[0-9]+[、.．\s]+", "", line)
                fingerprint = hashlib.md5(re.sub(r"\s+", "", clean_title).encode("utf-8")).hexdigest()[:16]
                if fingerprint in seen_fingerprints:
                    continue
                seen_fingerprints.add(fingerprint)
                items.append(DeviationItem(
                    index=counter,
                    clause_title=clean_title,
                    is_star=is_star,
                    response_status="待生成",
                    response_detail="",
                    source_fingerprint=fingerprint,
                ))
                counter += 1
                if counter > max_items:
                    break

        # ★条款优先排序（红线条款置顶）
        items.sort(key=lambda x: not x.is_star)
        for i, item in enumerate(items, 1):
            item.index = i
        return items

    def coverage_report(self, items: List[DeviationItem], tender_text: str) -> dict:
        """覆盖率核算（借鉴 Web 版强规则弱模型模式的 covered/uncovered 核算）"""
        total_star_lines = sum(1 for l in (tender_text or "").split("\n") if "★" in l or l.strip().startswith("▲"))
        covered_star = sum(1 for it in items if it.is_star)
        return {
            "extracted_total": len(items),
            "star_in_tender": total_star_lines,
            "star_covered": covered_star,
            "star_uncovered": max(0, total_star_lines - covered_star),
            "note": "★号条款存在未覆盖行，建议人工核对招标文件红线条款" if total_star_lines > covered_star else "",
        }

    # ---------------- LLM 点对点响应 ----------------

    def generate_response_for_item(self, item: DeviationItem, facts: GlobalFacts) -> DeviationItem:
        """针对单个指标生成点对点应答（LLM 不可用时留空待生成，不编造）"""
        retrieval = retrieval_service.retrieve(query=item.clause_title, top_k=2, rerank=False)
        ref_context = retrieval["refs"][0]["content"][:250] if retrieval["refs"] else ""

        from app.services.assets.asset_manager import asset_manager
        matched_comp = asset_manager.match_assets_for_section(item.clause_title, [])
        comp_context = matched_comp.get("context_text", "")[:250]

        facts_filled = facts.filled_count() > 0
        facts_text = (
            f"投标企业【{facts.company_name}】，核心产品【{facts.core_product_name}】，"
            f"技术栈【{facts.architecture_stack}】，数据库【{facts.database_selection}】"
            if facts_filled else "（投标主体事实未配置——请给出通用但具体的技术路径表述，不得提及具体公司名）"
        )

        user_prompt = (
            f"【招标文件要求】：{item.clause_title}\n"
            f"【是否★号条款】：{'是（绝不可产生负偏离）' if item.is_star else '否'}\n"
            f"【我司全局事实】：{facts_text}\n"
            f"【企业中台可用资产】：{comp_context or '无匹配资产'}\n"
            f"【历史标书参考】：{ref_context or '无'}\n\n"
            f"请以 JSON 格式输出：\n"
            f'{{"response_status": "完全满足" 或 "正偏离", "response_detail": "点对点具体技术佐证阐述（50~120字）"}}'
        )

        if self.llm.is_configured:
            data = self.llm.chat_completion_structured(
                system_prompt=DEVIATION_PROMPT, user_prompt=user_prompt, temperature=0.2
            )
            if isinstance(data, dict) and data.get("response_detail") and self.llm.get_mode() == "llm":
                item.response_status = data.get("response_status", "完全满足")
                item.response_detail = str(data["response_detail"])[:300]
                return item
            # 普通文本兜底（仅接受真实模型输出，模拟器内容不得冒充响应声明）
            res = self.llm.chat_completion(system_prompt=DEVIATION_PROMPT, user_prompt=user_prompt, temperature=0.2)
            if res and len(res) > 10 and self.llm.get_mode() == "llm":
                item.response_detail = res.strip()[:300]
                item.response_status = "正偏离" if ("优于" in res or "正偏离" in res) else "完全满足"
                return item

        # LLM 不可用：留空待生成（不编造响应承诺）
        item.response_status = "待生成"
        item.response_detail = ""
        return item

    def batch_generate_responses(
        self, items: List[DeviationItem], facts: GlobalFacts, progress=None
    ) -> List[DeviationItem]:
        """批量生成技术偏离响应（支持进度上报与取消）"""
        total = len(items)
        for i, item in enumerate(items):
            if progress and progress.cancelled():
                break
            self.generate_response_for_item(item, facts)
            if progress:
                progress.report(int((i + 1) / max(total, 1) * 100), f"点对点响应 {i + 1}/{total}")
        return items

    @staticmethod
    def to_markdown_table(items: List[DeviationItem]) -> str:
        """将偏离表项转换为标准 Markdown 表格（总结语按实际状态生成，不无条件声明无偏离）"""
        lines = [
            "| 序号 | 招标文件技术规格要求 | 条款属性 | 投标响应状态 | 点对点详细技术方案与响应说明 |",
            "| :---: | :--- | :---: | :---: | :--- |",
        ]
        for it in items:
            star_label = "★号核心条款" if it.is_star else "一般技术条款"
            status_label = f"**{it.response_status}**"
            clean_clause = it.clause_title.replace("|", " ").replace("\n", " ")
            clean_detail = it.response_detail.replace("|", " ").replace("\n", " ")
            lines.append(f"| {it.index} | {clean_clause} | {star_label} | {status_label} | {clean_detail} |")

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
