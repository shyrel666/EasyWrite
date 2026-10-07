/**
 * 把文本按关键词切成片段，供模板逐段渲染（命中的片段用 <mark> 高亮），不拼接 HTML。
 * 关键词按长度降序匹配，较长的要点优先（"应急预案演练"先于"应急预案"）。
 */
export function highlightSegments(text, keywords = []) {
  const words = [...new Set((keywords || []).filter((k) => k && k.length >= 2))].sort((a, b) => b.length - a.length)
  if (!text || !words.length) return [{ text: text || '', hit: false }]
  const pattern = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'g')
  return text
    .split(pattern)
    .filter((part) => part !== '')
    .map((part) => ({ text: part, hit: words.includes(part) }))
}

/** 字数显示：1234 → 1.2k */
export function formatChars(n) {
  if (!n) return '0'
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}
