import { describe, expect, it } from 'vitest'
import { collapseRows, diffText, inlineParts } from '@/utils/textDiff'

describe('textDiff', () => {
  it('pairs modified lines with inline char changes', () => {
    const { rows, stats, changed } = diffText('第一段不变\n响应时间2小时\n', '第一段不变\n响应时间30分钟\n')
    expect(changed).toBe(true)
    expect(rows[0]).toEqual({ type: 'same', text: '第一段不变' })
    expect(rows[1].type).toBe('mod')
    const del = rows[1].parts.filter((p) => p.type === 'del').map((p) => p.text).join('')
    const add = rows[1].parts.filter((p) => p.type === 'add').map((p) => p.text).join('')
    expect(del).toContain('2小时')
    expect(add).toContain('30分钟')
    expect(stats.removed).toBeGreaterThan(0)
    expect(stats.added).toBeGreaterThan(0)
  })

  it('treats dissimilar lines as replace and handles empty sides', () => {
    expect(inlineParts('完全不同的旧句子', 'abc')).toBeNull()
    const { rows } = diffText('旧的整段内容', '全新写法xyz')
    expect(rows.map((r) => r.type)).toEqual(['del', 'add'])
    const added = diffText('', '新正文\n第二行')
    expect(added.rows.map((r) => r.type)).toEqual(['add', 'add'])
    expect(added.stats).toEqual({ added: 6, removed: 0 })
    expect(diffText('同样', '同样').changed).toBe(false)
  })

  it('collapses long unchanged runs keeping context', () => {
    const same = Array.from({ length: 10 }, (_, i) => ({ type: 'same', text: `行${i}` }))
    const rows = [...same, { type: 'add', text: '新增' }, ...same]
    const out = collapseRows(rows, 2)
    expect(out[0]).toEqual({ type: 'gap', count: 8 })
    expect(out.slice(1, 3).map((r) => r.text)).toEqual(['行8', '行9'])
    expect(out[3].type).toBe('add')
    expect(out.slice(4, 6).map((r) => r.text)).toEqual(['行0', '行1'])
    expect(out[6]).toEqual({ type: 'gap', count: 8 })
    expect(collapseRows([{ type: 'same', text: 'a' }], 2)).toEqual([{ type: 'same', text: 'a' }])
  })
})
