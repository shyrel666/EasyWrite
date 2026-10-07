<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import { renderMermaidSvg } from '@/utils/mermaid'

const props = defineProps({
  content: { type: String, default: '' },
  mode: { type: String, default: 'preview' }, // 'preview' 公文预览 | 'plain' 通用
  dense: { type: Boolean, default: false }, // 窄容器（抽屉）里收紧纸面边距
})

const md = new MarkdownIt({ html: false, breaks: true, linkify: true })

// 自定义围栏：mermaid 代码块渲染为真实 SVG 图
const defaultFence = md.renderer.rules.fence
md.renderer.rules.fence = (tokens, idx, options, env, self) => {
  const token = tokens[idx]
  const code = token.content.trim()
  if (token.info && token.info.trim().startsWith('mermaid')) {
    const id = `mmd-${Math.random().toString(36).slice(2, 9)}`
    // 图题：Mermaid 前置元数据里的 title（导出 Word 时同样用它作题注）
    const front = code.match(/^---\s*\n([\s\S]*?)\n---/)
    const title = front && (front[1].match(/^\s*title:\s*(.+?)\s*$/m) || [])[1]
    const caption = '图：' + md.utils.escapeHtml((title || '架构图').replace(/^['"]|['"]$/g, ''))
    return `<figure class="mermaid-figure" data-mermaid-id="${id}">\n<pre class="mermaid">${md.utils.escapeHtml(code)}</pre>\n<figcaption>${caption}</figcaption>\n</figure>`
  }
  return defaultFence ? defaultFence(tokens, idx, options, env, self) : `<pre><code>${md.utils.escapeHtml(code)}</code></pre>`
}

const el = ref(null)
const html = computed(() => md.render(props.content || ''))

async function renderMermaids() {
  if (!el.value) return
  const blocks = el.value.querySelectorAll('pre.mermaid')
  for (const block of blocks) {
    const code = block.textContent || ''
    try {
      const svg = await renderMermaidSvg(code)
      const wrapper = document.createElement('div')
      wrapper.innerHTML = svg
      wrapper.style.display = 'inline-block'
      wrapper.style.maxWidth = '100%'
      block.replaceWith(wrapper)
    } catch {
      block.textContent = '（架构图渲染失败：' + code.split('\n')[0] + ' …）'
    }
  }
}

onMounted(() => nextTick(renderMermaids))
watch(html, () => nextTick(renderMermaids))
</script>

<template>
  <div class="md-render h-full overflow-auto" :class="mode === 'preview' ? (dense ? 'p-3' : 'px-2 py-3 sm:px-6 sm:py-8') : ''">
    <div
      ref="el"
      :class="mode === 'preview' ? ['doc-page bid-doc-preview', dense ? '!px-7 !py-8' : ''] : 'text-sm leading-relaxed'"
      v-html="html"
    />
  </div>
</template>
