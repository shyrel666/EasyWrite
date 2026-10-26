"""评分项响应索引及 Word 内部链接，页码始终由排版引擎计算。"""
import hashlib

from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from app.services.assets.evidence import resolve_links
from app.services.assets.material_check import asset_name
from app.services.generator.rubric_planner import target_items


def bookmark_name(kind, ident):
    return f"ew_{kind}_" + hashlib.sha256(ident.encode()).hexdigest()[:28]


def bookmark(paragraph, name, number):
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), str(number))
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), str(number))
    paragraph._p.insert(0, start)
    paragraph._p.append(end)


def pageref(paragraph, name):
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), f" PAGEREF {name} \\h ")
    field.set(qn("w:dirty"), "true")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "更新域"
    run.append(text)
    field.append(run)
    paragraph._p.append(field)


def scoring_index_rows(project, snapshot=None):
    def walk(nodes):
        for n in nodes:
            yield n
            yield from walk(n.children)

    nodes = list(walk(project.outline))
    rows = []
    for item in target_items(project.tender_analysis):
        linked, missing = resolve_links(project.evidence_links.get(item.id, []), snapshot)
        attachments = [{"asset_key": f"{kind}:{a['id']}", "asset_name": asset_name(kind, a),
                        "id": f.get("id", ""), "name": f.get("filename", f.get("name", "附件"))}
                       for kind, a in linked for f in a.get("attachments", [])]
        sections = [{"id": n.id, "title": n.title, "bookmark": bookmark_name("s", n.id),
                     "written": bool(n.content.strip())} for n in nodes if item.id in n.scoring_item_ids]
        rows.append({"id": item.id, "name": item.name, "points": item.points, "package": item.package,
                     "sections": sections, "attachments": attachments, "missing_links": missing,
                     "bookmark": bookmark_name("i", item.id)})
    return rows
