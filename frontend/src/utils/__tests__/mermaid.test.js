import { describe, expect, it, vi } from 'vitest'

// Mermaid 体积大且依赖真实浏览器排版：这里只验证代码块提取与去重逻辑
vi.mock('mermaid', () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async () => {
      throw new Error('jsdom 中不渲染')
    }),
  },
}))

const { extractMermaidBlocks, renderOutlineDiagrams } = await import('@/utils/mermaid')

const FLOW = 'graph TD\n  A[网关] --> B[服务]\n'
const SEQ = 'sequenceDiagram\n  用户->>系统: 登录\n'

describe('Mermaid 代码块提取', () => {
  it('只取 mermaid 围栏（含 ~~~ 与大小写），忽略其他代码块', () => {
    const md = [
      '## 架构', '```mermaid', FLOW.trimEnd(), '```',
      '```python', 'print(1)', '```',
      '~~~Mermaid', SEQ.trimEnd(), '~~~',
    ].join('\n')
    expect(extractMermaidBlocks(md)).toEqual([FLOW, SEQ])
    expect(extractMermaidBlocks('')).toEqual([])
    expect(extractMermaidBlocks('普通正文')).toEqual([])
  })

  it('整本标书去重后逐张渲染，失败的图计数且不影响其余', async () => {
    const outline = [
      { content: '```mermaid\n' + FLOW + '```', children: [
        { content: '同一张图再出现一次\n```mermaid\n' + FLOW + '```' },
        { content: '```mermaid\n' + SEQ + '```' },
      ] },
      { content: '没有图' },
    ]
    const progress = []
    const res = await renderOutlineDiagrams(outline, (i, n) => progress.push([i, n]))
    expect(res.total).toBe(2)
    expect(res.failed).toBe(2)
    expect(res.diagrams).toEqual([])
    expect(progress).toEqual([[1, 2], [2, 2]])
  })
})
