import { describe, expect, it } from 'vitest'
import { formatChars, highlightSegments } from '@/utils/highlight'

describe('highlightSegments', () => {
  it('按关键词切分，命中的片段标记 hit', () => {
    expect(highlightSegments('须制定应急预案并演练', ['应急预案'])).toEqual([
      { text: '须制定', hit: false },
      { text: '应急预案', hit: true },
      { text: '并演练', hit: false },
    ])
  })

  it('较长的要点优先，正则特殊字符按原文匹配', () => {
    const parts = highlightSegments('软、硬件的运维方案（含应急预案演练）', ['应急预案', '应急预案演练', '软、硬件的运维方案（含'])
    expect(parts.filter((p) => p.hit).map((p) => p.text)).toEqual(['软、硬件的运维方案（含', '应急预案演练'])
  })

  it('没有关键词或空文本时原样返回', () => {
    expect(highlightSegments('正文', [])).toEqual([{ text: '正文', hit: false }])
    expect(highlightSegments('', ['x1'])).toEqual([{ text: '', hit: false }])
  })

  it('字数', () => {
    expect(formatChars(0)).toBe('0')
    expect(formatChars(860)).toBe('860')
    expect(formatChars(12345)).toBe('12.3k')
  })
})
