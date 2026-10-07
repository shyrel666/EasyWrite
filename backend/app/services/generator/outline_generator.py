"""
技术标大纲规划引擎（评分项对齐 + 一级确认门 + 字数预算）。

流程（借鉴 OpenBidKit outlineGenerationTaskV2 的人工在环设计）：
1. draft_level1：基于 18 项拆标（尤其评分办法与★条款）生成一级章节草案，
   供用户在向导中确认/增删/改名——硬性人工门，避免整树返工
2. expand_full_tree：以确认后的一级章节为骨架展开二级/三级树，
   按评分比重分配字数预算（max×0.8÷叶数，对冲 AI 系统性超写）
3. 知识条目元数据路由：大纲阶段只看条目 title/summary，由 LLM 判断哪些章节
   可获得知识库支撑（避免整文档注入的 Token 浪费）
"""
import logging
from typing import Dict, Any, List, Optional, Tuple

from app.core.llm_client import llm_client
from app.models.schemas import OutlineNode, TenderAnalysis18, GlobalFacts, ScoringItem
from app.services.rag import curator
from app.services.generator import rubric_planner as rp

logger = logging.getLogger("easywrite.outline")


def _assign_paths(nodes: List[OutlineNode], parent_path: str = ""):
    for node in nodes:
        node.path = f"{parent_path} > {node.title}" if parent_path else node.title
        _assign_paths(node.children, node.path)


def _count_leaves(nodes: List[OutlineNode]) -> int:
    return sum(_count_leaves(n.children) if n.children else 1 for n in nodes)


LEVEL1_SYSTEM = """你是一名从业20年的国家级招投标规划专家兼资深总架构师。
请根据招标项目信息，设计技术标书的一级章节结构（仅一级，不含子章节）。

【设计原则】：
1. 评分项对齐：技术标书的章节必须覆盖招标文件技术评分办法中的每一个评分项，
   高分值评分项应设独立章节重点响应；★号条款必须有明确承接章节。
2. 涵盖常规核心篇章：项目理解、总体技术方案、详细功能设计、实施方案、售后保障等，
   按本项目实际取舍，不堆砌无关章节。
3. 每个一级章节需给出 requirements（该章须响应的招标评分点/要求，来自招标文件原文要点）。
4. 若提供了【评分细则】：每个评分项都必须由某个章节承接，并在该章 scoring_item_ids 中填写评分项 ID；
   "撰写方案"类评分项原则上一项一章（章节标题贴合评分项名称，便于评审专家对照评分标准找分），
   其评分要点留到展开阶段作为二级章节，不要把一个评分项的要点拆成多个一级章节。
5. 只输出一个 JSON 对象：{"chapters": [{"title": "第一章 XXX", "requirements": ["..."], "scoring_item_ids": ["s_s2"]}]}
"""

EXPAND_SYSTEM = """你是一名资深技术标书总架构师。请基于已确认的一级章节骨架，展开完整大纲树（二级为主，关键复杂章节可到三级）。

【展开原则】：
1. 严格以给定一级章节为骨架，不得增删或改名一级章节。
2. 二级章节要具体可写：标题体现明确的交付物/设计对象，避免"相关设计"这类空泛标题；
   章节附有评分子项/评分要点时，二级章节应与之一一对应（标题即评分点）。
3. 涉及总体架构的章节要求包含 Mermaid 拓扑图（在 requirements 中注明）。
4. 字数预算：按一级章节的评分比重分配 word_budget（叶节点级，正文字数）。
5. content_mode 分配：常规论述=ai_generate；逐条响应指标清单的=point_to_point。
6. 只输出一个 JSON 对象：
{"outline": [{"id": "sec_1", "title": "沿用一级标题", "level": 1, "word_budget": 8000,
  "children": [{"id": "sec_1_1", "title": "1.1 XXX", "level": 2, "word_budget": 2000,
   "requirements": ["..."], "content_mode": "ai_generate"}]}]}"""


class OutlineGenerator:
    def __init__(self):
        self.llm = llm_client

    # ---------------- 一级草案（确认门之前） ----------------

    def draft_level1(
        self,
        description: str,
        tender_analysis: Optional[TenderAnalysis18] = None,
        facts: Optional[GlobalFacts] = None,
    ) -> Dict[str, Any]:
        """生成一级章节草案（供向导确认），返回 {chapters, mode, message}"""
        items = rp.target_items(tender_analysis)
        context = self._build_context(description, tender_analysis, facts)
        item_meta = curator.build_item_metadata_prompt()
        if item_meta:
            context += "\n\n" + item_meta

        if self.llm.is_configured:
            data = self.llm.chat_completion_structured(
                system_prompt=LEVEL1_SYSTEM,
                user_prompt=context,
                temperature=0.3,
                purpose="outline_draft",
            )
            chapters = []
            if isinstance(data, dict) and isinstance(data.get("chapters"), list):
                for ch in data["chapters"]:
                    if isinstance(ch, dict) and ch.get("title"):
                        chapters.append({
                            "title": str(ch["title"]).strip(),
                            "requirements": [str(r) for r in (ch.get("requirements") or [])][:8],
                            "scoring_item_ids": [str(x) for x in (ch.get("scoring_item_ids") or [])],
                        })
            if chapters and self.llm.get_mode() == "llm":
                message = ""
                if items:
                    chapters, missing = rp.ensure_coverage(chapters, items)
                    if missing:
                        message = f"大模型规划遗漏 {len(missing)} 个评分项，已自动补充承接章节：{'、'.join(missing)}"
                return {"chapters": chapters, "mode": "llm", "message": message}

        reason = "大模型规划失败" if self.llm.is_configured else "未配置大模型"
        if items:
            return {
                "chapters": rp.rubric_chapters(items),
                "mode": "rules",
                "message": f"{reason}：已按评分细则逐项生成一级章节（需撰写方案的评分项独立成章），可手动调整",
            }
        return {
            "chapters": self._default_level1(),
            "mode": "rules",
            "message": f"{reason}且未识别到评分细则：已载入标准软件技术标一级结构（可在确认后手动调整）",
        }

    # ---------------- 展开全树（确认门之后） ----------------

    def expand_full_tree(
        self,
        confirmed_chapters: List[Dict[str, Any]],
        description: str,
        tender_analysis: Optional[TenderAnalysis18] = None,
        facts: Optional[GlobalFacts] = None,
        total_word_budget: int = 30000,
    ) -> Dict[str, Any]:
        """以确认的一级章节为骨架展开完整大纲树，返回 {outline, mode, message}"""
        items_by_id = {it.id: it for it in rp.target_items(tender_analysis)}
        for ch in confirmed_chapters:
            ch["scoring_item_ids"] = [x for x in (ch.get("scoring_item_ids") or []) if x in items_by_id]

        def describe(ch: Dict[str, Any]) -> str:
            line = f"- {ch['title']}（响应要点：{'；'.join(ch.get('requirements', [])[:3]) or '无'}）"
            for it in (items_by_id[x] for x in ch["scoring_item_ids"]):
                points = "、".join(s.name for s in it.sub_items) or "、".join(rp.content_points(it.criteria)[:6])
                line += f"\n    · 承接评分项 {it.name}（{rp.fmt_points(it.points)}）" + (f"，评分要点：{points}" if points else "")
            return line

        skeleton = "\n".join(describe(ch) for ch in confirmed_chapters)
        context = (
            f"{self._build_context(description, tender_analysis, facts, include_rubric=False)}\n\n"
            f"【已确认的一级章节骨架（不得改动）】：\n{skeleton}\n\n"
            f"【全书正文总字数预算】：约 {total_word_budget} 字"
        )

        if self.llm.is_configured:
            data = self.llm.chat_completion_structured(
                system_prompt=EXPAND_SYSTEM,
                user_prompt=context,
                temperature=0.3,
                purpose="outline_expand",
            )
            outline = self._parse_tree(data, confirmed_chapters) if self.llm.get_mode() == "llm" else None
            if outline:
                child_weights: Dict[str, float] = {}
                for i, node in enumerate(outline, 1):
                    if not node.children:  # 大模型未展开的章节用评分细则补齐
                        node.children, weights = rp.chapter_children(i, confirmed_chapters[i - 1], items_by_id)
                        child_weights.update(weights)
                self._allocate_budgets(outline, total_word_budget, items_by_id, child_weights)
                _assign_paths(outline)
                return {"outline": outline, "mode": "llm", "message": ""}

        outline, child_weights = self._fallback_tree(confirmed_chapters, items_by_id)
        self._allocate_budgets(outline, total_word_budget, items_by_id, child_weights)
        _assign_paths(outline)
        reason = "大模型展开失败" if self.llm.is_configured else "未配置大模型"
        return {
            "outline": outline,
            "mode": "rules",
            "message": f"{reason}：已按评分子项/评分要点生成二级结构" if items_by_id
            else f"{reason}：已按一级骨架生成通用二级结构",
        }

    # ---------------- 内部方法 ----------------

    @staticmethod
    def _build_context(description: str, tender_analysis: Optional[TenderAnalysis18], facts: Optional[GlobalFacts],
                       include_rubric: bool = True) -> str:
        parts = [f"【项目背景与需求】：\n{description or '（未提供）'}"]
        if tender_analysis:
            ta = tender_analysis
            scoring = (
                f"评标办法：{ta.scoring_method or '未提及'}；价格分：{ta.price_score_weight or '未提及'}；"
                f"技术分：{ta.tech_score_weight or '未提及'}；商务分：{ta.business_score_weight or '未提及'}"
            )
            parts.append(f"【招标评分信息】：\n{scoring}")
            rubric = rp.rubric_prompt(rp.target_items(ta)) if include_rubric else ""
            if rubric:
                parts.append(rubric)
            if ta.star_disqualification_items:
                stars = "\n".join(f"- {s}" for s in ta.star_disqualification_items[:15])
                parts.append(f"【★号不可偏离条款（必须有章节承接）】：\n{stars}")
            if ta.duration_requirement and ta.duration_requirement != "未提及":
                parts.append(f"【工期要求】：{ta.duration_requirement}")
        if facts and facts.filled_count() > 0:
            parts.append(f"【投标主体事实】：\n{facts.to_constraint_text()}")
        return "\n\n".join(parts)

    def _parse_tree(self, data, confirmed_chapters: List[Dict[str, Any]]) -> Optional[List[OutlineNode]]:
        """解析 LLM 树输出；一级标题强制对齐用户确认的骨架"""
        if not (isinstance(data, dict) and isinstance(data.get("outline"), list)):
            return None
        confirmed_titles = [ch["title"] for ch in confirmed_chapters]
        nodes: List[OutlineNode] = []
        raw_top = [n for n in data["outline"] if isinstance(n, dict)]

        # 对齐：按顺序匹配 LLM 输出与确认骨架
        for i, title in enumerate(confirmed_titles):
            raw = raw_top[i] if i < len(raw_top) else {}
            children = []
            for j, sub in enumerate(raw.get("children") or [], 1):
                if not isinstance(sub, dict) or not sub.get("title"):
                    continue
                sub_node = OutlineNode(
                    id=f"sec_{i + 1}_{j}",
                    title=str(sub["title"]).strip(),
                    level=2,
                    requirements=[str(r) for r in (sub.get("requirements") or [])][:6],
                    word_budget=sub.get("word_budget"),
                    content_mode=sub.get("content_mode") if sub.get("content_mode") in ("ai_generate", "point_to_point", "template_fill") else "ai_generate",
                )
                # 三级节点
                for k, sub3 in enumerate(sub.get("children") or [], 1):
                    if isinstance(sub3, dict) and sub3.get("title"):
                        sub_node.children.append(OutlineNode(
                            id=f"sec_{i + 1}_{j}_{k}",
                            title=str(sub3["title"]).strip(),
                            level=3,
                            requirements=[str(r) for r in (sub3.get("requirements") or [])][:4],
                            content_mode=sub3.get("content_mode") or "ai_generate",
                        ))
                children.append(sub_node)
            node = OutlineNode(
                id=f"sec_{i + 1}",
                title=title,  # 强制使用确认后的标题
                level=1,
                requirements=[str(r) for r in (confirmed_chapters[i].get("requirements") or [])][:8],
                scoring_item_ids=confirmed_chapters[i].get("scoring_item_ids", []),
                children=children,
            )
            nodes.append(node)
        return nodes if nodes else None

    @staticmethod
    def _allocate_budgets(outline: List[OutlineNode], total: int,
                          items_by_id: Optional[Dict[str, ScoringItem]] = None,
                          child_weights: Optional[Dict[str, float]] = None):
        """
        字数预算（AI 系统性超写 ~25%，故目标 = 总量×0.8）：
        - 有评分细则：按章节承接的分值 × 应答方式系数分配（证书清单/应答表不需要方案级篇幅），
          未承接评分项的章节取最小正权重的一半；章内按评分子项分值（无则均分）分到叶节点
        - 无评分细则：按叶节点数均分
        """
        budget = total * 0.8
        items_by_id = items_by_id or {}
        child_weights = child_weights or {}
        shares: Dict[str, int] = {}
        for n in outline:
            for x in set(n.scoring_item_ids):
                shares[x] = shares.get(x, 0) + 1
        weights = [rp.chapter_word_weight(n, items_by_id, shares) for n in outline]
        positives = [w for w in weights if w > 0]
        if positives:
            floor = min(positives) * 0.5
            weights = [w if w > 0 else floor for w in weights]
        else:
            weights = [float(_count_leaves([n]) or 1) for n in outline]
        total_weight = sum(weights) or 1.0
        for n, w in zip(outline, weights):
            n.word_budget = int(round(budget * w / total_weight / 100) * 100)
            leaves = [leaf for c in n.children for leaf in (c.children or [c])]
            if not leaves:
                continue
            leaf_w = [child_weights.get(leaf.id, 1.0) for leaf in leaves]
            for leaf, lw in zip(leaves, leaf_w):
                leaf.word_budget = max(200, int(round(n.word_budget * lw / sum(leaf_w) / 50) * 50))

    @staticmethod
    def _default_level1() -> List[Dict[str, Any]]:
        return [
            {"title": "第一章 项目理解与建设目标", "requirements": ["深刻理解业务痛点与技术现状"]},
            {"title": "第二章 总体技术架构与方案设计", "requirements": ["总体逻辑架构、技术路线选型、高可用设计"]},
            {"title": "第三章 详细功能与业务实现方案", "requirements": ["对照招标功能清单逐项阐述"]},
            {"title": "第四章 项目实施与交付进度管理", "requirements": ["实施组织、里程碑工期、质量保证"]},
            {"title": "第五章 售后服务保障与培训体系", "requirements": ["SLA响应、巡检机制、培训计划"]},
        ]

    @staticmethod
    def _fallback_tree(confirmed_chapters: List[Dict[str, Any]],
                       items_by_id: Dict[str, ScoringItem]) -> Tuple[List[OutlineNode], Dict[str, float]]:
        """确定性展开：有评分项的章节按评分子项/要点拆分，其余用通用三段式"""
        nodes, child_weights = [], {}
        for i, ch in enumerate(confirmed_chapters, 1):
            children, weights = rp.chapter_children(i, ch, items_by_id)
            if not children:
                children = [
                    OutlineNode(id=f"sec_{i}_1", title=f"{i}.1 建设背景与需求理解", level=2),
                    OutlineNode(id=f"sec_{i}_2", title=f"{i}.2 总体设计方案", level=2),
                    OutlineNode(id=f"sec_{i}_3", title=f"{i}.3 实施保障措施", level=2),
                ]
            child_weights.update(weights)
            nodes.append(OutlineNode(
                id=f"sec_{i}", title=ch["title"], level=1,
                requirements=ch.get("requirements", []), children=children,
                scoring_item_ids=ch.get("scoring_item_ids", []),
            ))
        return nodes, child_weights


outline_generator = OutlineGenerator()
