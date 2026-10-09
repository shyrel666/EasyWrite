// 正文字数（与字数预算比较的口径）：不计空白、代码块（Mermaid 图代码）、表格分隔行与 Markdown 标记符号。
// 与后端 app/services/text_stats.py 的 body_chars 同一口径，编辑器显示的字数与章节检查一致。
export function bodyChars(text) {
  return (text || '')
    .replace(/```[\s\S]*?(?:```|$)/g, '')
    .replace(/^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*$/gm, '')
    .replace(/^[ \t]*[-+*][ \t]+/gm, '')
    .replace(/[|*#>`]/g, '')
    .replace(/\s/g, '').length
}
