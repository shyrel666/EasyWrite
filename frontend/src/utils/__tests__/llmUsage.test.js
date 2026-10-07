import { describe, expect, it } from 'vitest'
import { callStatusMeta, fillDays, formatMs, formatTokens, purposeLabel } from '@/utils/llmUsage'

describe('模型调用记录格式化', () => {
  it('Token 数：千分位、十万以上用"万"，未返回用量显示破折号', () => {
    expect(formatTokens(0)).toBe('0')
    expect(formatTokens(12345)).toBe('12,345')
    expect(formatTokens(123456)).toBe('12.3 万')
    expect(formatTokens(null)).toBe('—')
    expect(formatTokens(undefined)).toBe('—')
  })

  it('耗时：一秒以内用毫秒', () => {
    expect(formatMs(850)).toBe('850 ms')
    expect(formatMs(12340)).toBe('12.3 s')
    expect(formatMs(null)).toBe('—')
  })

  it('用途与状态名称', () => {
    expect(purposeLabel('section_write')).toBe('章节撰写')
    expect(purposeLabel('')).toBe('其他')
    expect(purposeLabel('new_purpose')).toBe('new_purpose')
    expect(callStatusMeta('truncated')).toEqual({ label: '被截断', type: 'warning' })
    expect(callStatusMeta('aborted').label).toBe('已取消')
  })
})

describe('按日补齐', () => {
  it('补齐空缺日期并按时间顺序排列，跨月也连续', () => {
    const rows = fillDays([{ day: '2026-10-01', calls: 3, total_tokens: 900 }], 3, new Date(2026, 9, 2))
    expect(rows.map((r) => r.day)).toEqual(['2026-09-30', '2026-10-01', '2026-10-02'])
    expect(rows.map((r) => r.calls)).toEqual([0, 3, 0])
    expect(rows[1].total_tokens).toBe(900)
  })

  it('没有数据时全为 0', () => {
    expect(fillDays(undefined, 2, new Date(2026, 0, 1)).map((r) => r.calls)).toEqual([0, 0])
  })
})
