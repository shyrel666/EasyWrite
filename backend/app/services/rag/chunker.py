"""
结构感知语义分块器（RAG 入库第一站）。

设计依据：
- 保留 word_parser 的层级大纲 + 面包屑（本系统最有价值的结构资产）
- 在章节边界内做目标长度聚合（400–600 字目标 / 1000 上限），表格独立成块
- FB208 式块过滤：页码、目录点线、重复页眉页脚、过短残句
- 每块生成 context_text（文档名 | 面包屑 + 正文）作为嵌入输入——
  Anthropic Contextual Retrieval 的轻量实现（LLM 摘要增强见 enricher.py）
"""
import re
from typing import Dict, Any, List

from app.core.config import settings

# ---------------- 块过滤规则（FB208 式） ----------------

_RE_PAGE_NUMBER = re.compile(r"^第?\s*\d+\s*页(\s*共\s*\d+\s*页?)?$|^\d+\s*/\s*\d+$")
_RE_CATALOG_LINE = re.compile(r".{2,60}[…\.·]{6,}.{0,30}$")
_RE_WHITESPACE = re.compile(r"\s+")


def _is_noise_paragraph(text: str, noise_counter: Dict[str, int]) -> bool:
    """判断段落是否为应过滤的噪声（页码/目录行/重复页眉页脚）"""
    stripped = text.strip()
    if not stripped:
        return True
    if _RE_PAGE_NUMBER.match(stripped):
        return True
    if _RE_CATALOG_LINE.match(stripped) and len(stripped) < 90:
        return True
    # 归一化后 ≤40 字且出现 ≥4 次的短行 → 重复页眉/页脚
    normalized = _RE_WHITESPACE.sub("", stripped)
    if len(normalized) <= 40:
        noise_counter[normalized] = noise_counter.get(normalized, 0) + 1
        if noise_counter[normalized] >= 4:
            return True
    return False


def _table_to_markdown(tbl: List[List[str]]) -> str:
    if not tbl or not tbl[0]:
        return ""
    header = tbl[0]
    sep = ["---"] * len(header)
    md_rows = ["| " + " | ".join(header) + " |", "| " + " | ".join(sep) + " |"]
    for row in tbl[1:]:
        padded = row + [""] * (len(header) - len(row))
        md_rows.append("| " + " | ".join(padded[: len(header)]) + " |")
    return "\n".join(md_rows)


# ---------------- 标签规则（廉价的关键词分类，供检索过滤） ----------------

_KEYWORD_TAGS = {
    "架构": ["系统架构", "技术路线"],
    "安全": ["安全方案", "等保", "数据安全"],
    "运维": ["售后服务", "运维保障", "SLA"],
    "实施": ["项目实施", "进度管理", "部署方案"],
    "测试": ["软件测试", "压力测试", "验收标准"],
    "培训": ["人员培训", "交付培训"],
    "信创": ["信创适配", "国产化"],
    "数据库": ["数据库设计", "容灾备份"],
    "微服务": ["微服务", "容器化", "Kubernetes"],
}


def _extract_tags(breadcrumb: str, content_head: str) -> List[str]:
    tags = []
    for kw, mapped in _KEYWORD_TAGS.items():
        if kw in breadcrumb or kw in content_head:
            tags.extend(mapped)
    return sorted(set(tags))


def _build_context_text(doc_name: str, breadcrumb: str, content: str, summary: str = "") -> str:
    """嵌入输入：文档名 | 面包屑 [+ LLM 一句话定位摘要] + 正文（Anthropic 上下文增强）"""
    header = f"《{doc_name}》"
    if breadcrumb:
        header += f" {breadcrumb}"
    if summary:
        header += f"\n本节要点：{summary}"
    return f"{header}\n{content}"


def chunk_parsed_document(
    parsed_doc: Dict[str, Any],
    doc_name: str,
    doc_id: str,
) -> List[Dict[str, Any]]:
    """
    将 word_parser 解析出的大纲树转为语义分块列表。

    返回块结构：{id, seq, section_title, breadcrumb, content, context_text,
                tags, parent_summary}
    """
    target = settings.RAG_CHUNK_TARGET_CHARS
    max_len = settings.RAG_CHUNK_MAX_CHARS
    min_len = settings.RAG_CHUNK_MIN_CHARS

    noise_counter: Dict[str, int] = {}
    chunks: List[Dict[str, Any]] = []

    def emit(section_title: str, breadcrumb: str, text: str, parent_summary: str):
        text = text.strip()
        if not text:
            return
        seq = len(chunks)
        content = text[:max_len * 2]  # 硬性保险，正常不会触发
        chunks.append({
            "id": f"{doc_id}_c{seq}",
            "seq": seq,
            "section_title": section_title,
            "breadcrumb": breadcrumb,
            "content": content,
            "context_text": _build_context_text(doc_name, breadcrumb, content),
            "tags": _extract_tags(breadcrumb, content[:120]),
            "parent_summary": parent_summary,
        })

    def split_long_text(section_title: str, breadcrumb: str, paragraphs: List[str], parent_summary: str):
        """按目标长度聚合段落；超长段落按句子切分"""
        buf: List[str] = []
        buf_len = 0

        def flush():
            nonlocal buf, buf_len
            if buf:
                emit(section_title, breadcrumb, "\n".join(buf), parent_summary)
            buf, buf_len = [], 0

        for para in paragraphs:
            plen = len(para)
            if buf_len + plen > target and buf_len >= min_len:
                flush()
            if plen > max_len:
                # 超长段落：按中文句号/分号切分重组
                flush()
                sentences = re.split(r"(?<=[。；！？\n])", para)
                sbuf: List[str] = []
                slen = 0
                for s in sentences:
                    if slen + len(s) > target and slen > 0:
                        emit(section_title, breadcrumb, "".join(sbuf), parent_summary)
                        sbuf, slen = [], 0
                    sbuf.append(s)
                    slen += len(s)
                if sbuf:
                    emit(section_title, breadcrumb, "".join(sbuf), parent_summary)
                continue
            buf.append(para)
            buf_len += plen
        flush()

    def traverse(node: Dict[str, Any], parent_summary: str):
        title = node.get("title", "")
        breadcrumb = node.get("breadcrumb", "") or title
        content = node.get("content", "")

        # 本节父摘要 = 上一级章节标题 + 首段前 200 字（供检索后上下文扩展）
        own_summary = f"{title}：{content[:200]}" if content else title

        paragraphs = [p for p in content.split("\n") if p and not _is_noise_paragraph(p, noise_counter)]
        if paragraphs:
            split_long_text(title, breadcrumb, paragraphs, parent_summary)

        # 表格独立成块（表格是标书中的关键结构化资产）
        for tbl in node.get("tables", []):
            md = _table_to_markdown(tbl)
            if md:
                emit(f"{title}（表格）", breadcrumb, md, parent_summary)

        for sub in node.get("subsections", []):
            traverse(sub, own_summary)

    for sec in parsed_doc.get("sections", []):
        traverse(sec, "")

    # 尾部合并：仅合并同一小节内的过小片段（跨小节绝不合并——面包屑归属是检索精度
    # 的核心资产，小章节独立成块优于并入邻章，内容零丢失）
    if len(chunks) >= 2:
        merged: List[Dict[str, Any]] = [chunks[0]]
        for c in chunks[1:]:
            if (
                len(c["content"]) < min_len
                and c["section_title"] == merged[-1]["section_title"]
                and len(merged[-1]["content"]) < max_len
            ):
                merged[-1]["content"] += "\n" + c["content"]
                merged[-1]["context_text"] = _build_context_text(
                    doc_name, merged[-1]["breadcrumb"], merged[-1]["content"]
                )
            else:
                merged.append(c)
        chunks = merged

    return chunks
