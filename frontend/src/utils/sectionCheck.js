/**
 * 章节检查报告的展示辅助：问题分组、来源/待核实类别名称、在正文中定位原文片段。
 */
export const ISSUE_SOURCE = {
  rule: { label: '规则', chip: 'chip-mute' },
  llm: { label: '模型（参考）', chip: 'chip-accent' },
}

export const VERIFY_KIND = {
  commitment: '承诺数值',
  placeholder: '占位',
  unverified_asset: '待核实资料',
}

/** { blocking, quality }：阻塞问题与质量问题各自的列表（保持后端顺序） */
export function groupIssues(report) {
  const issues = report?.issues || []
  return {
    blocking: issues.filter((i) => i.level === 'blocking'),
    quality: issues.filter((i) => i.level === 'quality'),
  }
}

/** 报告摘要胶囊：无阻塞为绿色，否则红色 */
export function reportVerdict(report) {
  if (!report) return null
  if (report.blocking_count) return { label: `阻塞 ${report.blocking_count}`, chip: 'chip-bad' }
  return { label: '无阻塞问题', chip: 'chip-ok' }
}

/**
 * 原文片段在正文中的位置 [start, end]；片段两端的省略号去掉后查找，找不到返回 null。
 * line（从 1 开始）用于区分重复出现的片段：优先取该行内的匹配。
 */
export function locateExcerpt(content, excerpt, line) {
  const needle = (excerpt || '').replace(/^…+|…+$/g, '').trim()
  if (!content || !needle) return null
  if (line) {
    const lines = content.split('\n')
    if (line <= lines.length) {
      const offset = lines.slice(0, line - 1).reduce((n, l) => n + l.length + 1, 0)
      const i = lines[line - 1].indexOf(needle)
      if (i >= 0) return [offset + i, offset + i + needle.length]
    }
  }
  const i = content.indexOf(needle)
  return i >= 0 ? [i, i + needle.length] : null
}
