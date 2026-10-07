/**
 * Mermaid 统一配置 + 导出用 PNG 渲染。
 * 预览与导出共用同一份配置：标签用 SVG 文本而不是 HTML（foreignObject 画进 canvas 会污染画布、无法导出图片），
 * 这样预览看到的图就是导出到 Word 里的图。
 */
import mermaid from 'mermaid'
import MarkdownIt from 'markdown-it'

mermaid.initialize({
  startOnLoad: false,
  theme: 'neutral',
  securityLevel: 'loose',
  fontFamily: '"Microsoft YaHei", "PingFang SC", sans-serif',
  htmlLabels: false,
  flowchart: { htmlLabels: false },
})

export default mermaid

let seq = 0

/** 前置元数据里的 title 由图题（预览 figcaption / Word 题注）承担，图内不再重复画一遍 */
function stripTitle(code) {
  return code.replace(/^(\s*---\s*\n)([\s\S]*?)(\n---\s*\n)/, (all, open, body, close) => {
    const rest = body.split('\n').filter((line) => !/^\s*title\s*:/.test(line))
    return rest.some((line) => line.trim()) ? open + rest.join('\n') + close : ''
  })
}

/** 渲染为 SVG 字符串（失败抛出 Mermaid 的语法错误） */
export async function renderMermaidSvg(code) {
  seq += 1
  const id = `mmd-${Date.now()}-${seq}`
  try {
    const { svg } = await mermaid.render(id, stripTitle(code))
    return svg
  } finally {
    // 语法错误时 Mermaid 会把报错图残留在 body 里
    document.getElementById(`d${id}`)?.remove()
  }
}

/**
 * Mermaid 代码 → PNG data URL（白底，scale 倍像素，后端按 2 倍像素换算 Word 中的尺寸）
 */
export async function renderMermaidPng(code, scale = 2) {
  const svg = await renderMermaidSvg(code)
  const svgDoc = new DOMParser().parseFromString(svg, 'image/svg+xml')
  const el = svgDoc.documentElement
  const viewBox = (el.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number)
  const width = Math.ceil(viewBox[2] || parseFloat(el.getAttribute('width')) || 800)
  const height = Math.ceil(viewBox[3] || parseFloat(el.getAttribute('height')) || 600)
  el.setAttribute('width', String(width))
  el.setAttribute('height', String(height))
  el.removeAttribute('style') // 去掉 max-width 等响应式样式，按真实尺寸绘制
  const xml = new XMLSerializer().serializeToString(el)

  const img = new Image()
  img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(xml)
  await img.decode()
  const canvas = document.createElement('canvas')
  canvas.width = width * scale
  canvas.height = height * scale
  const ctx = canvas.getContext('2d')
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.scale(scale, scale)
  ctx.drawImage(img, 0, 0, width, height)
  return canvas.toDataURL('image/png')
}

const md = new MarkdownIt({ html: false, breaks: true })

/** 从正文 Markdown 中取出全部 mermaid 代码块（与后端 markdown-it 解析一致） */
export function extractMermaidBlocks(markdown) {
  if (!markdown || !markdown.includes('mermaid')) return []
  return md
    .parse(markdown, {})
    .filter((t) => t.type === 'fence' && (t.info || '').trim().toLowerCase().startsWith('mermaid'))
    .map((t) => t.content)
}

/**
 * 渲染整本标书的全部架构图：返回 { diagrams: [{code, image}], failed: 失败数 }。
 * 单张失败（语法错误等）不影响其余，后端会为失败的图留出代码插槽。
 */
export async function renderOutlineDiagrams(outline, onProgress) {
  const codes = []
  const walk = (nodes) => {
    for (const n of nodes || []) {
      for (const code of extractMermaidBlocks(n.content)) {
        if (!codes.includes(code)) codes.push(code)
      }
      walk(n.children)
    }
  }
  walk(outline)
  const diagrams = []
  let failed = 0
  for (let i = 0; i < codes.length; i++) {
    if (onProgress) onProgress(i + 1, codes.length)
    try {
      diagrams.push({ code: codes[i], image: await renderMermaidPng(codes[i]) })
    } catch {
      failed += 1
    }
  }
  return { diagrams, failed, total: codes.length }
}
