"""
正文字数（与字数预算比较的口径）：不计空白、代码块（Mermaid 图代码）、表格分隔行与 Markdown 标记符号。
前端 utils/wordCount.js 的 bodyChars 与此同一口径，编辑器显示的字数与章节检查一致。
"""
import re

CODE_FENCE = re.compile(r"```.*?(?:```|$)", re.S)
TABLE_RULE = re.compile(r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*$", re.M)
BULLET = re.compile(r"^[ \t]*[-+*][ \t]+", re.M)
MARKUP = re.compile(r"[|*#>`]")
WHITESPACE = re.compile(r"\s+")


def body_chars(text: str) -> int:
    t = CODE_FENCE.sub("", text or "")
    t = TABLE_RULE.sub("", t)
    t = BULLET.sub("", t)
    t = MARKUP.sub("", t)
    return len(WHITESPACE.sub("", t))
