"""
按章节读取招标原文（数据来自项目存档的 tender_structure_json，即解析器的章节树）。

- outline_tree：章节树（ID、标题、层级、路径、字数），供工作台"招标原文"页签浏览
- section_text：某一章节的正文（含表格行；默认连同下级章节），调用方按 offset/limit 分段读取
- node_keywords / related_sections：当前撰写章节的要点（标题、评分要点、评分子项）及其在招标文件中出现的章节，
  用于高亮与快速定位；只做原文子串匹配，不做推测
"""
import re
from typing import Any, Dict, Iterator, List, Optional

from app.models.schemas import OutlineNode, TenderAnalysis18
from app.services.generator import rubric_planner as rp

REQ_PREFIX = re.compile(r"^(?:评分项|须覆盖|评分要点|评分子项|所属评分项|证明材料)[：:]\s*")
POINTS_SUFFIX = re.compile(r"[（(][^（()）]*分[^（()）]*[）)]$")
TITLE_NUMBER = re.compile(r"^(?:第[一二三四五六七八九十百\d]+[章节篇部分]+|[\d.]+|[一二三四五六七八九十]+[、.．])\s*")
MAX_KEYWORDS = 12


def _table_lines(tables: List[List[List[str]]]) -> List[str]:
    return [" | ".join(c for c in row if c) for table in tables for row in table if any(row)]


def own_text(section: Dict[str, Any]) -> str:
    """章节自身的正文与表格（不含下级章节）"""
    parts = [section.get("content", "").strip()] + _table_lines(section.get("tables") or [])
    return "\n".join(p for p in parts if p)


def _chars(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def walk(sections: List[Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
    for s in sections:
        yield s
        yield from walk(s.get("subsections") or [])


def outline_tree(structure: Optional[dict]) -> List[Dict[str, Any]]:
    def build(s: Dict[str, Any]) -> Dict[str, Any]:
        children = [build(c) for c in s.get("subsections") or []]
        chars = _chars(own_text(s))
        return {
            "id": s.get("section_id", ""),
            "title": s.get("title", ""),
            "level": s.get("level", 1),
            "path": s.get("breadcrumb", "") or s.get("title", ""),
            "chars": chars,
            "total_chars": chars + sum(c["total_chars"] for c in children),
            "children": children,
        }

    return [build(s) for s in (structure or {}).get("sections", [])]


def find_section(structure: Optional[dict], key: str) -> Optional[Dict[str, Any]]:
    """按章节 ID 查找；找不到时按完整路径（面包屑）查找"""
    sections = list(walk((structure or {}).get("sections", [])))
    return (next((s for s in sections if s.get("section_id") == key), None)
            or next((s for s in sections if s.get("breadcrumb") == key), None))


def section_text(section: Dict[str, Any], deep: bool = True) -> str:
    """章节正文；deep 时依次附上下级章节（以"## 标题"行分隔）"""
    parts = [own_text(section)]
    if deep:
        for child in walk(section.get("subsections") or []):
            parts.append(f"## {child.get('title', '')}")
            parts.append(own_text(child))
    return "\n\n".join(p for p in parts if p)


def _clean_phrase(text: str) -> str:
    text = REQ_PREFIX.sub("", (text or "").strip())
    text = POINTS_SUFFIX.sub("", text).strip(" ：:；;，,。")
    return TITLE_NUMBER.sub("", text).strip()


def _phrases(candidates: List[str]) -> List[str]:
    out: List[str] = []
    for c in candidates:
        phrase = _clean_phrase(c)
        if 2 <= len(phrase) <= 20 and phrase not in out:
            out.append(phrase)
    return out


def own_keywords(node: OutlineNode) -> List[str]:
    """章节自身的要点：标题与本节要求（评分要点、所属评分项等）"""
    return _phrases([node.title] + list(node.requirements))


def node_keywords(node: OutlineNode, ta: Optional[TenderAnalysis18]) -> List[str]:
    """当前章节的招标要点：自身要点在前，其后是所承接评分项的名称、子项与列举要点（去编号与分值，2~20 字）"""
    candidates = [node.title] + list(node.requirements)
    items = {it.id: it for it in rp.target_items(ta)}
    for sid in node.scoring_item_ids:
        it = items.get(sid)
        if it:
            candidates += [it.name] + [s.name for s in it.sub_items] + rp.content_points(it.criteria)
    return _phrases(candidates)[:MAX_KEYWORDS]


def related_sections(structure: Optional[dict], keywords: List[str], limit: int = 8,
                     primary: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """要点在原文中出现的章节：标题命中权重 3、正文命中权重 1，章节自身要点（primary）加倍，按得分降序"""
    if not keywords:
        return []
    primary_set = set(primary or [])
    scored = []
    for s in walk((structure or {}).get("sections", [])):
        title, body = s.get("title", ""), own_text(s)
        hits = [k for k in keywords if k in title or k in body]
        if not hits:
            continue
        score = sum(((3 if k in title else 0) + (1 if k in body else 0)) * (2 if k in primary_set else 1)
                    for k in hits)
        scored.append((score, {"id": s.get("section_id", ""), "title": title,
                               "path": s.get("breadcrumb", "") or title, "hits": hits}))
    scored.sort(key=lambda t: -t[0])
    return [x for _, x in scored[:limit]]
