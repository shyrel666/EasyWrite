/**
 * 正文差异（候选稿与当前正文对比）：按行对齐，修改过的行再逐字标出增删。
 * 使用本地安装的 diff 包，不走 CDN。
 */
import { diffChars, diffLines } from 'diff'

// 修改行内逐字对比的相似度下限：低于它视为整行替换（逐字标出反而难读）
const INLINE_MIN_SIMILARITY = 0.3
const LINE_TIMEOUT_MS = 1500
const CHAR_TIMEOUT_MS = 200

function splitLines(value) {
  const lines = value.split('\n')
  if (lines.length && lines[lines.length - 1] === '') lines.pop()
  return lines
}

function countChars(text) {
  return (text || '').replace(/\s/g, '').length
}

/** 行内逐字差异 [{type: same|add|del, text}]；相似度太低或超时返回 null */
export function inlineParts(oldLine, newLine) {
  const changes = diffChars(oldLine, newLine, { timeout: CHAR_TIMEOUT_MS })
  if (!changes) return null
  const same = changes.filter((c) => !c.added && !c.removed).reduce((n, c) => n + c.value.length, 0)
  if (same / Math.max(oldLine.length, newLine.length, 1) < INLINE_MIN_SIMILARITY) return null
  return changes.map((c) => ({ type: c.added ? 'add' : c.removed ? 'del' : 'same', text: c.value }))
}

/**
 * 逐行差异：rows 为 [{type: same|add|del|mod, text?, parts?}]，mod 行带行内 parts；
 * stats 为新增 / 删除的非空白字符数。
 */
export function diffText(oldText, newText) {
  const a = oldText || ''
  const b = newText || ''
  const changes = diffLines(a, b, { timeout: LINE_TIMEOUT_MS }) || [
    { removed: true, value: a }, { added: true, value: b },
  ]
  const rows = []
  const stats = { added: 0, removed: 0 }
  for (let i = 0; i < changes.length; i += 1) {
    const c = changes[i]
    if (!c.added && !c.removed) {
      for (const line of splitLines(c.value)) rows.push({ type: 'same', text: line })
      continue
    }
    if (c.removed && changes[i + 1]?.added) {
      // 删除块紧跟新增块：逐行配对为"修改"
      const olds = splitLines(c.value)
      const news = splitLines(changes[i + 1].value)
      const n = Math.max(olds.length, news.length)
      for (let j = 0; j < n; j += 1) {
        const o = olds[j]
        const w = news[j]
        if (o !== undefined && w !== undefined) {
          const parts = inlineParts(o, w)
          if (parts) {
            rows.push({ type: 'mod', parts })
            for (const p of parts) {
              if (p.type === 'add') stats.added += countChars(p.text)
              if (p.type === 'del') stats.removed += countChars(p.text)
            }
            continue
          }
        }
        if (o !== undefined) {
          rows.push({ type: 'del', text: o })
          stats.removed += countChars(o)
        }
        if (w !== undefined) {
          rows.push({ type: 'add', text: w })
          stats.added += countChars(w)
        }
      }
      i += 1
      continue
    }
    for (const line of splitLines(c.value)) {
      rows.push({ type: c.added ? 'add' : 'del', text: line })
      if (c.added) stats.added += countChars(line)
      else stats.removed += countChars(line)
    }
  }
  return { rows, stats, changed: stats.added + stats.removed > 0 || rows.some((r) => r.type !== 'same') }
}

/** 折叠连续未变化的行：变化处前后各保留 context 行，其余替换为 {type: 'gap', count} */
export function collapseRows(rows, context = 2) {
  const out = []
  let run = []
  const flush = (atStart, atEnd) => {
    const keepHead = atStart ? 0 : context
    const keepTail = atEnd ? 0 : context
    if (run.length > keepHead + keepTail + 1) {
      out.push(...run.slice(0, keepHead))
      out.push({ type: 'gap', count: run.length - keepHead - keepTail })
      out.push(...run.slice(run.length - keepTail))
    } else {
      out.push(...run)
    }
    run = []
  }
  rows.forEach((r) => {
    if (r.type === 'same') {
      run.push(r)
      return
    }
    flush(out.length === 0, false)
    out.push(r)
  })
  flush(out.length === 0, true)
  return out
}
