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

it('长标签完整传给渲染器，导出使用中性主题并输出 PNG', async () => {
  const { default: mermaid, configureMermaid, renderMermaidPng } = await import('@/utils/mermaid')
  configureMermaid('dark')
  expect(mermaid.initialize).toHaveBeenLastCalledWith(expect.objectContaining({ theme: 'dark' }))
  mermaid.render.mockResolvedValueOnce({ svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 200"><text>长标签</text></svg>' })
  const previousImage = globalThis.Image
  globalThis.Image = class { async decode() {} }
  const context = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ fillRect: vi.fn(), scale: vi.fn(), drawImage: vi.fn() })
  const output = vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue('data:image/png;base64,test')
  try {
    const code = 'graph TD\nA[完整保留的非常长的业务节点标签不能被截断] --> B[终点]'
    expect(await renderMermaidPng(code)).toBe('data:image/png;base64,test')
    expect(mermaid.render).toHaveBeenLastCalledWith(expect.any(String), code)
    expect(mermaid.initialize).toHaveBeenLastCalledWith(expect.objectContaining({ theme: 'neutral' }))
  } finally { globalThis.Image = previousImage; context.mockRestore(); output.mockRestore() }
})
