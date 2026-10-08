"""
章节候选稿：AI 对已有正文的改动先存为候选稿，用户查看差异与检查报告后再采纳或放弃。

- 创建（propose）：对候选文本做规则检查（C1）、计算输入清单，状态为 checked；同一章节其他待处理的候选稿标为 superseded
- 输入清单（compute_manifest）：生成时实际使用的依据的内容指纹（SHA-256）——全局事实、本节承接的评分项、
  评分项关联的资料、知识库引用的锁定与排除、本节标题路径与字数预算、所用企业资料（逐条）。由服务端计算，不接受模型自述
- 采纳（apply_proposal）：在 project_store.update 的同一写锁与事务内依次判断——已采纳则直接返回原结果（候选稿 ID 即幂等键）；
  修订号与生成时不同 → 409 正文已变化；按当前状态重算的输入清单与记录不一致 → 409 依据已变化；
  否则写入正文（修订号 +1、来源 proposal）、历史版本快照与候选稿 applied 状态，三者同一事务提交
"""
import hashlib
import json
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlmodel import Session, select, update

from app.db.database import get_session
from app.db.models import SectionProposal
from app.models.schemas import OutlineNode, Project
from app.services.assets.asset_manager import asset_manager
from app.services.assets.evidence import make_key, parse_key
from app.services.assets.material_check import asset_name
from app.services.checker.section_check import check_section
from app.services.generator import rubric_planner as rp
from app.services.generator.evidence_set import EvidenceSet
from app.services.project_store import find_node, now_str
from app.services.section_content import save_section_content

OPEN_STATUSES = ("draft", "checked")
# 智能完善产生的候选稿：起草稿与各轮修订稿（parent_id 串成一条链）
REFINE_ORIGINS = ("refine_draft", "refine")
# 资料指纹不含附件与确认时间：补传证书附件不改变正文依据；状态（待核实 → 已确认）属于依据
ASSET_VOLATILE_FIELDS = ("attachments", "confirmed_at")
ASSET_PREFIX = "asset:"


# ---------------- 输入清单 ----------------

def _sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def section_path(outline: List[OutlineNode], section_id: str) -> str:
    """本节标题路径：祖先章节标题 > 本节标题"""
    def walk(nodes, chain):
        for n in nodes:
            if n.id == section_id:
                return chain + [n.title]
            found = walk(n.children, chain + [n.title])
            if found:
                return found
        return None
    return " > ".join(walk(outline, []) or [])


def evidence_asset_keys(evidence: EvidenceSet) -> List[str]:
    """本次用到的企业资料与因过期、主体不符被排除的资料（排除的资料恢复可用时依据也算变化）"""
    keys = [make_key(a["kind"], a["asset_id"]) for a in evidence.assets + evidence.excluded_assets]
    return list(dict.fromkeys(keys))


def compute_manifest(
    project: Project, node: OutlineNode,
    evidence: Optional[EvidenceSet] = None, asset_keys: Optional[List[str]] = None,
) -> Dict[str, Dict[str, str]]:
    """
    输入清单 {键: {label, sha}}。生成时传 evidence（取其中用到的资料）；采纳时传记录中的 asset_keys，
    逐条按当前资料库重新计算（资料被删除时 sha 为 deleted）。
    """
    items = {it.id: it for it in rp.target_items(project.tender_analysis)}
    manifest = {
        "facts": {"label": "全局事实", "sha": _sha(project.facts.model_dump())},
        "scoring_items": {"label": "本节承接的评分项",
                          "sha": _sha([items[x].model_dump() if x in items else x for x in node.scoring_item_ids])},
        "evidence_links": {"label": "评分项关联的企业资料",
                           "sha": _sha({x: sorted(project.evidence_links.get(x, [])) for x in node.scoring_item_ids})},
        "ref_settings": {"label": "知识库引用的锁定与排除",
                         "sha": _sha({"pinned": sorted(node.pinned_refs), "excluded": sorted(node.excluded_refs)})},
        "section": {"label": "本节标题路径与字数预算",
                    "sha": _sha({"path": section_path(project.outline, node.id), "word_budget": node.word_budget})},
    }
    keys = asset_keys if asset_keys is not None else (evidence_asset_keys(evidence) if evidence else [])
    for key in keys:
        kind, asset_id = parse_key(key)
        asset = asset_manager.get_asset(kind, asset_id)
        if asset is None:
            manifest[ASSET_PREFIX + key] = {"label": f"企业资料（已删除）：{asset_id}", "sha": "deleted"}
            continue
        fingerprint = {k: v for k, v in asset.items() if k not in ASSET_VOLATILE_FIELDS}
        manifest[ASSET_PREFIX + key] = {"label": f"企业资料：{asset_name(kind, asset)}", "sha": _sha(fingerprint)}
    return manifest


def manifest_asset_keys(manifest: Dict[str, Dict[str, str]]) -> List[str]:
    return [k[len(ASSET_PREFIX):] for k in manifest if k.startswith(ASSET_PREFIX)]


def diff_manifest(old: Dict[str, Dict[str, str]], new: Dict[str, Dict[str, str]]) -> List[Dict[str, str]]:
    """变化项 [{key, label}]（按记录中的顺序；新增的依据项排在后面）"""
    changes = []
    for key in list(old) + [k for k in new if k not in old]:
        a, b = old.get(key), new.get(key)
        if (a or {}).get("sha") != (b or {}).get("sha"):
            changes.append({"key": key, "label": (b or a or {}).get("label", key)})
    return changes


# ---------------- 仓储 ----------------

def to_dict(row: SectionProposal, with_content: bool = True) -> Dict[str, Any]:
    report = json.loads(row.report_json) if row.report_json else None
    data = {
        "id": row.id, "project_id": row.project_id, "section_id": row.section_id,
        "parent_id": row.parent_id, "task_id": row.task_id, "origin": row.origin,
        "base_revision": row.base_revision, "status": row.status,
        "refine": json.loads(row.refine_json) if row.refine_json else None,
        "created_at": row.created_at, "decided_at": row.decided_at,
        "char_count": len("".join((row.content or "").split())),
        "blocking_count": report.get("blocking_count", 0) if report else None,
        "quality_count": report.get("quality_count", 0) if report else None,
    }
    if with_content:
        data.update(content=row.content, report=report,
                    evidence=json.loads(row.evidence_json or "[]"),
                    input_manifest=json.loads(row.input_manifest_json or "{}"))
    return data


class ProposalStore:
    def create(self, row: SectionProposal) -> SectionProposal:
        """写入候选稿，并把同一章节其他待处理的候选稿标为 superseded（同一事务）"""
        with get_session() as session:
            self._supersede(session, row.project_id, [row.section_id])
            session.add(row)
            session.flush()
            session.expunge(row)
        return row

    def get(self, project_id: str, proposal_id: str) -> Optional[SectionProposal]:
        with get_session() as session:
            row = session.get(SectionProposal, proposal_id)
            if row is None or row.project_id != project_id:
                return None
            session.expunge(row)
            return row

    def list(self, project_id: str, section_id: Optional[str] = None, open_only: bool = False) -> List[SectionProposal]:
        with get_session() as session:
            query = select(SectionProposal).where(SectionProposal.project_id == project_id)
            if section_id is not None:
                query = query.where(SectionProposal.section_id == section_id)
            if open_only:
                query = query.where(SectionProposal.status.in_(OPEN_STATUSES))  # type: ignore[attr-defined]
            rows = session.exec(query.order_by(SectionProposal.created_ts.desc())).all()  # type: ignore[attr-defined]
            for r in rows:
                session.expunge(r)
            return list(rows)

    @staticmethod
    def _supersede(session: Session, project_id: str, section_ids: Optional[List[str]]):
        query = select(SectionProposal).where(SectionProposal.project_id == project_id,
                                              SectionProposal.status.in_(OPEN_STATUSES))  # type: ignore[attr-defined]
        if section_ids is not None:
            query = query.where(SectionProposal.section_id.in_(section_ids))  # type: ignore[attr-defined]
        for row in session.exec(query).all():
            row.status = "superseded"
            row.decided_at = now_str()
            session.add(row)

    def supersede(self, project_id: str, section_ids: Optional[List[str]] = None):
        """大纲重新生成（section_ids 为 None：全部）或删除章节后，相关的待处理候选稿作废"""
        if section_ids == []:
            return
        with get_session() as session:
            self._supersede(session, project_id, section_ids)

    @staticmethod
    def set_status_in(session: Session, proposal_id: str, status: str):
        """在调用方事务内修改状态（采纳时与正文同事务提交）"""
        row = session.get(SectionProposal, proposal_id)
        if row is None:
            raise KeyError(proposal_id)
        row.status = status
        row.decided_at = now_str()
        session.add(row)

    def close(self, proposal_id: str, status: str) -> bool:
        """待处理的候选稿改为 status（单条 UPDATE，已采纳等终态不会被改写）；返回是否修改"""
        with get_session() as session:
            result = session.exec(
                update(SectionProposal)
                .where(SectionProposal.id == proposal_id, SectionProposal.status.in_(OPEN_STATUSES))  # type: ignore[attr-defined]
                .values(status=status, decided_at=now_str())
            )
            return bool(result.rowcount)

    def chain(self, project_id: str, tip_id: str, max_len: int = 20) -> List[SectionProposal]:
        """沿 parent_id 回溯的智能完善候选稿链（根 → tip），遇到非智能完善的候选稿或其他章节即停"""
        out: List[SectionProposal] = []
        row = self.get(project_id, tip_id)
        while row is not None and row.origin in REFINE_ORIGINS and len(out) < max_len:
            if out and row.section_id != out[-1].section_id:
                break
            out.append(row)
            row = self.get(project_id, row.parent_id) if row.parent_id else None
        return list(reversed(out))

    def delete_project(self, project_id: str):
        with get_session() as session:
            for row in session.exec(select(SectionProposal).where(SectionProposal.project_id == project_id)).all():
                session.delete(row)


proposal_store = ProposalStore()


# ---------------- 创建 / 详情 / 采纳 / 放弃 ----------------

def propose(
    project: Project, node: OutlineNode, content: str, evidence: EvidenceSet, *,
    origin: str, task_id: str = "", parent_id: str = "",
    refine: Optional[Dict[str, Any]] = None, base_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """
    生成候选稿。project / node 为生成时读取的快照：base_revision 取 node.revision，输入清单与检查报告都按该快照计算
    （生成期间用户改了正文或依据，采纳时会如实报告变化）。
    base_revision 显式传入时以其为准：智能完善中断后继续执行，候选稿仍以起始时的正文为基准。
    refine 为智能完善的轮次信息（存入 refine_json）。
    """
    try:
        report = check_section(project, node, content, evidence).model_dump_json()
        status = "checked"
    except Exception:  # 检查异常时候选稿保持未检查状态，不能采纳
        report, status = "", "draft"
    row = SectionProposal(
        id=f"prop_{uuid.uuid4().hex[:12]}", project_id=project.id, section_id=node.id,
        parent_id=parent_id, task_id=task_id, origin=origin, content=content,
        base_revision=node.revision if base_revision is None else base_revision,
        refine_json=json.dumps(refine, ensure_ascii=False) if refine else "",
        input_manifest_json=json.dumps(compute_manifest(project, node, evidence), ensure_ascii=False),
        evidence_json=json.dumps(evidence.ref_records(), ensure_ascii=False),
        report_json=report, status=status, created_at=now_str(), created_ts=time.time(),
    )
    return to_dict(proposal_store.create(row))


def _require(project_id: str, section_id: str, proposal_id: str) -> SectionProposal:
    row = proposal_store.get(project_id, proposal_id)
    if row is None or row.section_id != section_id:
        raise HTTPException(status_code=404, detail="候选稿不存在")
    return row


def proposal_state(project: Project, row: SectionProposal) -> Dict[str, Any]:
    """待处理候选稿与当前状态的比对：正文是否变化、依据变化项（采纳前提示，采纳时以服务端判断为准）"""
    node = find_node(project.outline, row.section_id)
    if node is None:
        return {"section_exists": False, "current_content": "", "current_revision": None,
                "content_changed": True, "basis_changes": []}
    stored = json.loads(row.input_manifest_json or "{}")
    changes = diff_manifest(stored, compute_manifest(project, node, asset_keys=manifest_asset_keys(stored)))
    return {"section_exists": True, "current_content": node.content, "current_revision": node.revision,
            "content_changed": node.revision != row.base_revision, "basis_changes": changes}


class AlreadyApplied(Exception):
    """写锁内发现候选稿已被采纳（并发的重复采纳）"""


STATUS_MESSAGES = {
    "draft": "候选稿尚未完成检查，不能采纳",
    "rejected": "候选稿已放弃",
    "superseded": "候选稿已被更新的候选稿取代",
}


def _conflict(reason: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=409, detail={"reason": reason, "message": message, **extra})


def _applied(row: SectionProposal, already: bool, node_status: Optional[str] = None) -> Dict[str, Any]:
    return {"status": "applied", "already_applied": already, "proposal_id": row.id,
            "section_id": row.section_id, "node_status": node_status, "content": row.content}


def apply_proposal(project_id: str, section_id: str, proposal_id: str) -> Dict[str, Any]:
    row = _require(project_id, section_id, proposal_id)
    if row.status == "applied":
        return _applied(row, already=True)
    stored = json.loads(row.input_manifest_json or "{}")
    asset_keys = manifest_asset_keys(stored)

    def check(project: Project, node: OutlineNode):
        current = proposal_store.get(project_id, proposal_id)  # 写锁内重新读取：重复采纳只有一次生效
        if current is None:
            raise HTTPException(status_code=404, detail="候选稿不存在")
        if current.status == "applied":
            raise AlreadyApplied()
        if current.status != "checked":
            raise _conflict("not_open", STATUS_MESSAGES.get(current.status, "候选稿不可采纳"), status=current.status)
        if node.revision != current.base_revision:
            raise _conflict("content_changed", "正文已变化：候选稿生成后，本节正文被修改过",
                            current_content=node.content, current_revision=node.revision)
        changes = diff_manifest(stored, compute_manifest(project, node, asset_keys=asset_keys))
        if changes:
            raise _conflict("basis_changed", "依据已变化：候选稿生成后，" + "、".join(c["label"] for c in changes) + "有变化",
                            changes=changes)

    def also(session: Session, info: Dict[str, Any]):
        proposal_store.set_status_in(session, proposal_id, "applied")

    try:
        node_status = save_section_content(
            project_id, section_id, row.content, "completed",
            last_refs=json.loads(row.evidence_json or "[]"), version_source="proposal", check=check, also=also,
        )
    except AlreadyApplied:
        return _applied(row, already=True)
    return _applied(row, already=False, node_status=node_status)


def reject_proposal(project_id: str, section_id: str, proposal_id: str) -> Dict[str, Any]:
    _require(project_id, section_id, proposal_id)
    if proposal_store.close(proposal_id, "rejected"):
        return {"status": "rejected", "proposal_id": proposal_id}
    row = _require(project_id, section_id, proposal_id)  # 已不在待处理状态（含并发采纳）：如实返回
    if row.status == "applied":
        raise _conflict("applied", "候选稿已采纳，不能放弃；如需回退请在历史版本中恢复", status=row.status)
    return {"status": row.status, "proposal_id": proposal_id}
