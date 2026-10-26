"""章节、全书、导出和评估共用的事项身份与出现位置聚合。"""
import hashlib
import json

from app.models.schemas import VerifyOccurrence


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def aggregate_verification(items, section_id="", section_title="", decisions=None):
    rows = [v.model_copy(deep=True) for v in items]
    for v in rows:
        if not v.occurrences:
            v.occurrences = [VerifyOccurrence(text=v.text, line=v.line)]
        for o in v.occurrences:
            o.section_id = o.section_id or section_id
            o.section_title = o.section_title or section_title
        v.stable_key = v.stable_key or f"{v.kind}:{fingerprint(v.text)}"

    # 标注内已有业务资料身份时，通用占位只是该事项的另一个识别结果。
    for v in rows:
        if v.kind != "placeholder":
            continue
        matches = []
        for a in rows:
            if not a.asset_key:
                continue
            if any(x.section_id == y.section_id and x.start is not None and y.start is not None
                   and x.start < y.end and y.start < x.end for x in v.occurrences for y in a.occurrences):
                matches.append(a)
        if len({a.asset_key for a in matches}) == 1:
            a = matches[0]
            v.stable_key, v.asset_key, v.basis_hash = a.stable_key, a.asset_key, a.basis_hash
            v.kind, v.note = a.kind, a.note

    grouped = {}
    for v in rows:
        if v.stable_key not in grouped:
            grouped[v.stable_key] = v
            continue
        target = grouped[v.stable_key]
        for o in v.occurrences:
            duplicate = next((x for x in target.occurrences if x.section_id == o.section_id
                              and ((x.start is not None and o.start is not None and x.start < o.end and o.start < x.end)
                                   or x.model_dump() == o.model_dump())), None)
            if duplicate is None:
                target.occurrences.append(o)
            elif o.start is not None and duplicate.start is not None:
                duplicate.start, duplicate.end = min(duplicate.start, o.start), max(duplicate.end, o.end)
    result = list(grouped.values())
    for v in result:
        decision = (decisions or {}).get(v.stable_key)
        if decision:
            v.decision_state = "confirmed" if decision.get("basis_hash") == v.basis_hash else "stale"
            v.decision_note = decision.get("note", "")
    return result


def verification_counts(items):
    rows = aggregate_verification(items)
    return {"item_count": len(rows), "occurrence_count": sum(len(v.occurrences) for v in rows)}


def project_verification(project, snapshot=None):
    from app.services.assets.asset_manager import asset_manager
    from app.services.checker import section_check as sc

    snapshot = snapshot if snapshot is not None else asset_manager.snapshot()
    rows = []
    for node in sc._flatten(project.outline):
        text = sc._mask(node.content, sc.CODE_BLOCK)
        _, commitments = sc._check_commitments(sc._mask(text, sc.PLACEHOLDER), project.facts)
        _, assets = sc._check_assets(text, snapshot)
        rows += aggregate_verification(commitments + assets + sc._placeholders(text), node.id, node.title)
    return aggregate_verification(rows, decisions=project.verification_decisions)
