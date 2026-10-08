import { describe, expect, it } from 'vitest'
import { groupIssues, locateExcerpt, reportVerdict } from '@/utils/sectionCheck'

describe('sectionCheck utils', () => {
  it('groups issues by level', () => {
    const report = { issues: [
      { level: 'quality', code: 'cliche' },
      { level: 'blocking', code: 'missing_point' },
      { level: 'quality', code: 'length' },
    ] }
    const g = groupIssues(report)
    expect(g.blocking.map((i) => i.code)).toEqual(['missing_point'])
    expect(g.quality.map((i) => i.code)).toEqual(['cliche', 'length'])
    expect(groupIssues(null)).toEqual({ blocking: [], quality: [] })
  })

  it('verdict reflects blocking count', () => {
    expect(reportVerdict({ blocking_count: 2 })).toEqual({ label: '阻塞 2', chip: 'chip-bad' })
    expect(reportVerdict({ blocking_count: 0 }).chip).toBe('chip-ok')
    expect(reportVerdict(null)).toBeNull()
  })

  it('locates excerpts, preferring the given line', () => {
    const content = '第一行：2小时响应\n第二行：2小时响应'
    expect(locateExcerpt(content, '…2小时响应', 2)).toEqual([14, 19])
    expect(locateExcerpt(content, '2小时响应…')).toEqual([4, 9])
    expect(locateExcerpt(content, '不存在', 1)).toBeNull()
    expect(locateExcerpt('', 'x')).toBeNull()
  })
})
