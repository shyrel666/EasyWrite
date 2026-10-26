"""补遗只在人工确认后覆盖明确条款；原文、基线和撤回链永久保留。"""
import hashlib
import json
import re
import uuid
from datetime import datetime

from fastapi import HTTPException

from app.models.schemas import ClauseOverride, TenderDocument
from app.services.parser.commitments import structured_requirements
from app.services.project_store import _walk, now_str

FIELDS = ("duration_requirement", "warranty_period", "submission_deadline", "budget_limit",
          "payment_milestones", "project_location", "bid_security", "bid_validity_period",
          "site_survey_rules", "qualification_thresholds", "star_disqualification_items", "important_items")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def register_document(project, text, kind, name, package, published_at, file_hash=""):
    if not text.strip() or len(text) > 200_000:
        raise HTTPException(400, "文件正文须为 1～200000 字")
    if kind != "original":
        try:
            datetime.fromisoformat(published_at)
        except ValueError as e:
            raise HTTPException(400, "发布日期格式应为 YYYY-MM-DD 或 ISO 时间") from e
    sha = file_hash or hashlib.sha256(text.encode()).hexdigest()
    versions = project.tender_versions
    existing = next((d for d in versions.documents if (d.sha256, d.kind, d.package) == (sha, kind, package)), None)
    if existing:
        return existing
    doc = TenderDocument(id="doc_" + uuid.uuid4().hex, kind=kind, name=name, package=package,
                         published_at=published_at, sha256=sha, version=len(versions.documents) + 1,
                         text=text, created_at=now_str())
    versions.documents.append(doc)
    return doc


def archive_original(project, text, parsed=None):
    if not text.strip():
        return
    versions = project.tender_versions
    if any(x.state == "confirmed" for x in versions.overrides):
        raise HTTPException(409, "已有生效补遗，请从补遗入口追加；撤回后才能重设原件")
    package = project.tender_analysis.target_package if project.tender_analysis else ""
    doc = register_document(project, text, "original", (parsed or {}).get("filename", "招标原件"),
                            package, "", (parsed or {}).get("file_sha256", ""))
    versions.active_document_id = doc.id


def clauses(project):
    analysis = project.tender_versions.base_analysis or project.tender_analysis
    if not analysis:
        return []
    rows = []
    for item in analysis.scoring_items:
        rows.append({"id": "scoring:" + item.id, "text": item.criteria, "label": item.name,
                     "package": item.package, "points": item.points, "scoring_item_id": item.id})
    for field in FIELDS:
        value = getattr(analysis, field)
        for i, text in enumerate(value if isinstance(value, list) else [value]):
            if text:
                rows.append({"id": f"field:{field}:{i}", "text": text, "label": type(analysis).model_fields[field].description or field,
                             "package": analysis.target_package, "points": None, "scoring_item_id": ""})
    deviations = project.tender_versions.base_deviations or project.deviation_matrix
    for item in deviations:
        key = item.source_fingerprint or digest([item.package, item.section, item.clause_title])
        rows.append({"id": "deviation:" + key, "text": item.clause_title, "label": item.section or "技术条款",
                     "package": item.package, "points": None, "scoring_item_id": ""})
    return rows


def effective_clauses(project, skip=""):
    rows = {r["id"]: {**r, "original_text": r["text"]} for r in clauses(project)}
    for ov in sorted(project.tender_versions.overrides, key=lambda x: x.confirmed_order):
        if ov.state != "confirmed" or ov.id == skip:
            continue
        row = rows.get(ov.clause_id)
        if row is None or ov.basis_hash != digest([row["text"], row["points"]]):
            raise HTTPException(409, "后续补遗依赖该版本，请先撤回后续覆盖或重新核对条款")
        row["text"] = (row["text"] + "\n" + ov.text).strip() if ov.action == "append" else ("" if ov.action == "revoke" else ov.text)
        if ov.points is not None:
            row["points"] = ov.points
    for row in rows.values():
        row["basis_hash"] = digest([row["text"], row["points"]])
        row["sections"] = [{"id": n.id, "title": n.title} for n in _walk(project.outline)
                           if row["scoring_item_id"] in n.scoring_item_ids or
                           (not row["scoring_item_id"] and n.content.strip())]
    return rows


def mapping_suggestions(project):
    """仅识别带引号的明确“原…修改为…”；模糊表述留给人工建立覆盖。"""
    rows = effective_clauses(project)
    result = []
    target = project.tender_analysis.target_package if project.tender_analysis else ""
    for doc in project.tender_versions.documents:
        if doc.kind == "original" or doc.package != target:
            continue
        for m in re.finditer(r'原[“"]([^”"\n]+)[”"](?:调整|修改|变更)为[“"]([^”"\n]+)[”"]', doc.text):
            for row in rows.values():
                if row["text"] == m[1] and row["package"] in ("", target):
                    result.append({"document_id": doc.id, "clause_id": row["id"], "action": "replace",
                                   "text": m[2], "source_excerpt": m[0], "source_page": 1})
    return result


def suggest(project, request):
    versions = project.tender_versions
    doc = next((d for d in versions.documents if d.id == request.document_id), None)
    if not doc or doc.kind == "original":
        raise HTTPException(400, "请选择本项目的补遗或澄清文件")
    row = effective_clauses(project).get(request.clause_id)
    target = project.tender_analysis.target_package if project.tender_analysis else ""
    if not row or doc.package != target or row["package"] not in ("", target) or request.package != target:
        raise HTTPException(400, "仅允许同项目、当前分包的明确条款覆盖")
    if request.basis_hash != row["basis_hash"]:
        raise HTTPException(409, "条款依据已变化，请刷新后核对")
    if not request.source_excerpt.strip() or request.source_excerpt not in doc.text:
        raise HTTPException(400, "来源摘录必须存在于所选补遗正文中")
    if request.action != "revoke" and not request.text.strip():
        raise HTTPException(400, "替代或追加文本不能为空")
    if request.points is not None and not row["scoring_item_id"]:
        raise HTTPException(400, "只有评分条款可以调整分值")
    if not any(x.state != "withdrawn" for x in versions.overrides):
        versions.base_analysis = project.tender_analysis.model_copy(deep=True)
        versions.base_deviations = [d.model_copy(deep=True) for d in project.deviation_matrix]
    ov = ClauseOverride(**request.model_dump(), id="ov_" + uuid.uuid4().hex, original_text=row["text"])
    versions.overrides.append(ov)
    return ov


def recompute(project, changed_clause):
    versions = project.tender_versions
    rows = effective_clauses(project)
    analysis = versions.base_analysis.model_copy(deep=True)
    kept = []
    for item in analysis.scoring_items:
        row = rows["scoring:" + item.id]
        if not row["text"]:
            continue
        if row["text"] != item.criteria:
            item.sub_items = []  # 原有细分评分点不再代表新标准
        item.criteria, item.points = row["text"], row["points"]
        kept.append(item)
    analysis.scoring_items = kept
    for field in FIELDS:
        value = getattr(analysis, field)
        if isinstance(value, list):
            setattr(analysis, field, [rows[f"field:{field}:{i}"]["text"] for i in range(len(value))
                                      if rows[f"field:{field}:{i}"]["text"]])
        elif value:
            setattr(analysis, field, rows[f"field:{field}:0"]["text"])
    analysis.commitment_requirements = []
    analysis.commitment_requirements = structured_requirements(analysis)
    project.tender_analysis = analysis
    # 保留当前人工响应；仅受影响条款重算要求并使结论失效。
    changed = rows[changed_clause]
    variants = {changed["original_text"], changed["text"]}
    for ov in versions.overrides:
        if ov.clause_id == changed_clause:
            variants.update((ov.original_text, ov.text, (ov.original_text + "\n" + ov.text).strip()))
    variants.add("【已撤销条款】" + changed["original_text"])
    for item in project.deviation_matrix:
        key = "deviation:" + (item.source_fingerprint or digest([item.package, item.section, item.clause_title]))
        if key == changed_clause or item.clause_title in variants:
            item.clause_title = changed["text"] or "【已撤销条款】" + changed["original_text"]
            item.response_status = "待核实"
    for n in _walk(project.outline):
        if any(s["id"] == n.id for s in changed["sections"]):
            n.reviewed_revision = None
            if n.status == "reviewed":
                n.status = "completed"


def decide(project, override_id, state, basis_hash):
    ov = next((x for x in project.tender_versions.overrides if x.id == override_id), None)
    if not ov:
        raise HTTPException(404, "覆盖记录不存在")
    if ov.state == state:
        return ov
    if state == "confirmed":
        if ov.state != "pending":
            raise HTTPException(409, "已撤回记录不可重签，请重新建立覆盖")
        row = effective_clauses(project).get(ov.clause_id)
        doc = next(d for d in project.tender_versions.documents if d.id == ov.document_id)
        if doc.package != project.tender_analysis.target_package or ov.package != doc.package:
            raise HTTPException(409, "当前分包已变化，请重新建立覆盖")
        if not row or row["basis_hash"] != basis_hash or ov.basis_hash != basis_hash:
            raise HTTPException(409, "存在冲突补遗或条款已变化，请核对当前有效文本后重新建立覆盖")
        ov.confirmed_order = max((x.confirmed_order for x in project.tender_versions.overrides), default=0) + 1
    elif ov.state == "confirmed":
        effective_clauses(project, skip=ov.id)  # 有依赖时撤回失败，整个事务回滚
    ov.state, ov.decided_at = state, now_str()
    recompute(project, ov.clause_id)
    return ov


def effective_text(project, original):
    if not any(x.state == "confirmed" for x in project.tender_versions.overrides):
        return original
    rows = effective_clauses(project)
    for row in rows.values():
        if row["original_text"] and row["text"] != row["original_text"]:
            original = original.replace(row["original_text"], row["text"] or "【本条款已撤销】")
    changes = [f'{r["label"]}：{r["text"] or "已撤销"}' for r in rows.values() if r["text"] != r["original_text"]]
    return "【人工确认补遗后的有效要求】\n" + "\n".join(changes) + "\n\n" + original
