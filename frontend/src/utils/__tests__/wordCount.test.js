import { describe, expect, it } from 'vitest'
import { bodyChars } from '../wordCount'
import { SAMPLE, SAMPLE_BODY_CHARS } from './wordCountSample'

describe('bodyChars', () => {
  it('不计代码块、表格分隔行与 Markdown 符号', () => {
    expect(bodyChars(SAMPLE)).toBe(SAMPLE_BODY_CHARS)
  })

  it('未闭合的代码块算到文末', () => {
    expect(bodyChars('正文\n```mermaid\ngraph TD\nA-->B')).toBe(2)
  })

  it('空文本为 0', () => {
    expect(bodyChars('')).toBe(0)
    expect(bodyChars(null)).toBe(0)
  })
})
