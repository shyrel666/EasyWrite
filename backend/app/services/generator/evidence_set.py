"""
章节撰写的"选资料"环节：知识库引用 + 企业资料 + 相关招标原文 → EvidenceSet。

生成环节（section_generator.build_prompts）只接收 EvidenceSet、不再自行检索：
单章生成、流式生成、批量撰写都先调用 select_evidence，再用同一份资料装配提示词；
本次用到的知识库片段与企业资料随正文写入 last_refs，参考面板据此溯源。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.models.schemas import OutlineNode, Project
from app.services.assets.asset_manager import asset_manager
from app.services.assets.evidence import generation_filter, resolve_links
from app.services.assets.material_check import asset_name
from app.services.generator import rubric_planner as rp
from app.services.rag.retriever import retrieval_service

MAX_TENDER_SNIPPETS = 3
SNIPPET_CHARS = 600


@dataclass
class EvidenceSet:
    """一次章节撰写使用的全部依据"""
    # 知识库引用（检索管线 RetrievedRef.to_dict）
    refs: List[Dict[str, Any]] = field(default_factory=list)
    retrieval_message: str = ""
    # 企业资料：[{kind, asset_id, name, status, source}]，source 为 linked（评分项关联）/ matched（按章节主题匹配）
    assets: List[Dict[str, Any]] = field(default_factory=list)
    asset_context: str = ""
    # 已排除、未进入提示词的资料（证书过期 / 投标截止日前到期、所属主体与投标人不一致）：[{…, reason}]
    excluded_assets: List[Dict[str, Any]] = field(default_factory=list)
    # 相关招标原文：[{item_id, title, text}]（本节承接的评分项的评分标准原文）
    tender_snippets: List[Dict[str, str]] = field(default_factory=list)

    def reference_prompt(self) -> str:
        return retrieval_service.build_reference_prompt(self.refs)

    def tender_prompt(self) -> str:
        if not self.tender_snippets:
            return ""
        lines = ["【招标原文：本节承接的评分标准（逐条响应，不得改写为企业承诺）】："]
        lines += [f"- {s['title']}：{s['text']}" for s in self.tender_snippets]
        return "\n".join(lines)

    def ref_records(self) -> List[Dict[str, Any]]:
        """
        写入 last_refs 的溯源记录：知识库片段（ref_type=kb）、用到的企业资料（ref_type=asset）、
        因过期或主体不符被排除的资料（ref_type=asset_excluded，含 reason）
        """
        return ([dict(r, ref_type="kb") for r in self.refs]
                + [dict(a, ref_type="asset") for a in self.assets]
                + [dict(a, ref_type="asset_excluded") for a in self.excluded_assets])


def _asset_record(kind: str, item: Dict[str, Any], source: str) -> Dict[str, Any]:
    return {
        "kind": kind,
        "asset_id": item.get("id", ""),
        "name": asset_name(kind, item),
        "status": item.get("status", "confirmed"),
        "source": source,
    }


def _tender_snippets(project: Project, node: OutlineNode) -> List[Dict[str, str]]:
    items = {it.id: it for it in rp.target_items(project.tender_analysis)}
    out = []
    for sid in node.scoring_item_ids:
        it = items.get(sid)
        if not it or not (it.criteria or it.note):
            continue
        text = it.criteria.strip()
        if it.note.strip():
            text += f"（证明材料要求：{it.note.strip()}）"
        if len(text) > SNIPPET_CHARS:
            text = text[:SNIPPET_CHARS] + "…"
        out.append({"item_id": sid, "title": f"{it.name}（{rp.fmt_points(it.points)}）", "text": text})
        if len(out) >= MAX_TENDER_SNIPPETS:
            break
    return out


def select_evidence(
    project: Project,
    node: OutlineNode,
    instruction: str = "",
    *,
    section_title: Optional[str] = None,
    section_path: Optional[str] = None,
    requirements: Optional[List[str]] = None,
    pinned_refs: Optional[List[str]] = None,
    excluded_refs: Optional[List[str]] = None,
    retrieve: bool = True,
) -> EvidenceSet:
    """
    为一个章节挑选撰写依据（同步：检索含 LLM 重排，流式接口需放到线程中执行）：
    1. 知识库：完整检索管线（多查询 → 双路召回 → RRF → 重排 → 阈值），尊重锁定/排除
    2. 企业资料：本节评分项有用户关联的资料时只用关联资料，没有关联时按章节主题匹配相关条目。
       示例资料一律不用；证书过期（含投标截止日前到期）、所属主体与投标人不一致的资料不进入提示词，记入 excluded_assets
    3. 招标原文：本节承接的评分项的评分标准与证明材料要求
    参数为 None 时取章节自身的标题、路径、要求与锁定/排除设置。
    retrieve=False 时不检索知识库（章节检查等只需要企业资料与招标原文的场景，不产生重排调用）。
    """
    title = section_title or node.title
    path = section_path or node.path or title
    reqs = node.requirements if requirements is None else requirements

    retrieval = retrieval_service.retrieve(
        query=f"{title} {instruction}",
        section_title=title,
        section_path=path,
        requirements=reqs,
        project_context=f"{project.name}（客户：{project.client_name}）",
        top_k=4,
        pinned_ids=node.pinned_refs if pinned_refs is None else pinned_refs,
        excluded_ids=node.excluded_refs if excluded_refs is None else excluded_refs,
    ) if retrieve else {"refs": [], "message": "未检索知识库"}

    exclude = generation_filter(project)
    keys = list(dict.fromkeys(k for sid in node.scoring_item_ids for k in project.evidence_links.get(sid, [])))
    found, _missing = resolve_links(keys)
    if found:
        # 用户为本节评分项关联了资料：只用这些资料；全部被排除时不改用其他资料，只说明已排除
        asset_context, models, dropped = asset_manager.linked_context(found, exclude)
        assets = [_asset_record(kind, m.model_dump(), "linked") for kind, m in models]
        source = "linked"
    else:
        matched = asset_manager.match_assets_for_section(title, reqs, exclude=exclude)
        asset_context, dropped = matched.get("context_text", ""), matched.get("excluded", [])
        assets = [_asset_record(matched["kind"], item, "matched") for item in matched.get("items", [])]
        source = "matched"
    excluded = [dict(_asset_record(d["kind"], d["item"], source), reason=d["reason"]) for d in dropped]

    return EvidenceSet(
        refs=retrieval["refs"],
        retrieval_message=retrieval.get("message", ""),
        assets=assets,
        asset_context=asset_context,
        excluded_assets=excluded,
        tender_snippets=_tender_snippets(project, node),
    )
