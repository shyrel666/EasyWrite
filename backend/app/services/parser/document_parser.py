"""
上传文档统一入口：按扩展名分发到 Word / PDF 解析器，两者输出同构
（sections 章节树 / tables 扁平表格 / full_text 按序全文），下游无需区分来源格式。
"""
from pathlib import Path
from typing import Any, Dict, Optional

from app.services.parser.pdf_parser import PdfParseError, pdf_parser
from app.services.parser.word_parser import WordDocumentParser

SUPPORTED_SUFFIXES = (".docx", ".pdf")
SUPPORTED_LABEL = ".docx / .pdf"

_word_parser = WordDocumentParser()


def unsupported_reason(filename: Optional[str]) -> Optional[str]:
    """不支持的文件返回提示语，支持则返回 None"""
    suffix = Path(filename or "").suffix.lower()
    if suffix in SUPPORTED_SUFFIXES:
        return None
    if suffix in (".doc", ".wps"):
        return f"暂不支持 {suffix}（旧版 Word / WPS）格式，请另存为 .docx 后上传"
    return f"目前仅支持上传 {SUPPORTED_LABEL} 格式文件"


def parse_document(path) -> Dict[str, Any]:
    """解析 .docx / .pdf；PDF 无法提取文字（扫描件/加密/乱码）时抛出 PdfParseError（ValueError 子类，消息可直接展示）"""
    if Path(path).suffix.lower() == ".pdf":
        return pdf_parser.parse_pdf(path)
    result = _word_parser.parse_docx(path)
    result.setdefault("source_format", "docx")
    result.setdefault("warnings", [])
    return result


def describe_source(parsed: Dict[str, Any]) -> str:
    """来源说明，展示给用户：PDF 的页数、章节识别方式与告警"""
    if parsed.get("source_format") != "pdf":
        return ""
    mode = {"bookmarks": "按 PDF 书签识别章节", "layout": "PDF 无书签，按字号/加粗推断章节"}.get(
        parsed.get("heading_mode", ""), "")
    if parsed.get("heading_mode") == "layout" and parsed.get("bookmarks_ignored"):
        mode = "PDF 书签与正文章节对不上（多为 WPS 自动生成），已改为按字号/加粗推断章节"
    parts = [f"PDF 共 {parsed.get('page_count', 0)} 页" + (f"，{mode}" if mode else "")]
    parts.extend(parsed.get("warnings") or [])
    return "；".join(parts)


__all__ = ["SUPPORTED_SUFFIXES", "SUPPORTED_LABEL", "PdfParseError", "parse_document", "unsupported_reason",
           "describe_source"]
