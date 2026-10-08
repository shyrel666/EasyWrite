/**
 * 模型调用记录的展示辅助：用途/状态名称与数字格式化。
 */
export const PURPOSE_LABELS = {
  tender_extract: '拆标抽取',
  outline_draft: '大纲规划',
  outline_expand: '大纲展开',
  section_write: '章节撰写',
  polish: '润色',
  section_review: '章节评审',
  deviation_response: '偏离表响应',
  compliance_check: '合规核查',
  rerank: '检索重排',
  kb_enrich: '入库摘要',
  kb_curate: '条目策展',
  embedding: '向量嵌入',
  connection_test: '连接测试',
  other: '其他',
}

export const CALL_STATUS_META = {
  ok: { label: '成功', type: 'success' },
  truncated: { label: '被截断', type: 'warning' },
  empty: { label: '无正文', type: 'warning' },
  error: { label: '失败', type: 'danger' },
  aborted: { label: '已取消', type: 'info' },
}

export function purposeLabel(purpose) {
  return PURPOSE_LABELS[purpose] || purpose || '其他'
}

export function callStatusMeta(status) {
  return CALL_STATUS_META[status] || { label: status || '未知', type: 'info' }
}

/** Token 数：1234 → "1,234"，123456 → "12.3 万"；空值显示 "—"（服务商未返回用量） */
export function formatTokens(n) {
  if (n === null || n === undefined) return '—'
  if (n >= 100000) return `${(n / 10000).toFixed(1)} 万`
  return n.toLocaleString('en-US')
}

/** 毫秒：850 → "850 ms"，12340 → "12.3 s"，空值 "—" */
export function formatMs(ms) {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

function dayKey(d) {
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mm}-${dd}`
}

/**
 * 按日补齐：接口只返回有调用的日期，图表需要连续的 days 天（截至 today，含当天），空缺日记 0。
 */
export function fillDays(byDay, days, today = new Date()) {
  const known = new Map((byDay || []).map((r) => [r.day, r]))
  const out = []
  for (let i = days - 1; i >= 0; i -= 1) {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - i)
    const key = dayKey(d)
    const row = known.get(key)
    out.push({ day: key, calls: row?.calls || 0, total_tokens: row?.total_tokens || 0 })
  }
  return out
}
