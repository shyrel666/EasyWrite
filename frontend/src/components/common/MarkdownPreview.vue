<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import mermaid from 'mermaid'

const props = defineProps({
  content: { type: String, default: '' },
  mode: { type: String, default: 'preview' }, // 'preview' 公文预览 | 'plain' 通用
})

mermaid.initialize({ startOnLoad: false, theme: 'neutral', securityLevel: 'loose', fontFamily: 'inherit' })

const md = new MarkdownIt({ html: false, breaks: true, linkify: true })

// 自定义围栏：mermaid 代码块渲染为真实 SVG 图
const defaultFence = md.renderer.rules.fence
md.renderer.rules.fence = (tokens, idx, options, env, self) => {
  const token = tokens[idx]
  const code = token.content.trim()
  if (token.info && token.info.trim().startsWith('mermaid')) {
    const id = `mmd-${Math.random().toString(36).slice(2, 9)}`
    return `<figure class="mermaid-figure" data-mermaid-id="${id}">\n<pre class="mermaid">${md.utils.escapeHtml(code)}</pre>\n<figcaption>图：系统逻辑架构与数据流向图</figcaption>\n</figure>`
  }
  return defaultFence ? defaultFence(tokens, idx, options, env, self) : `<pre><code>${md.utils.escapeHtml(code)}</code></pre>`
}

const el = ref(null)
const html = computed(() => md.render(props.content || ''))

async function renderMermaids() {
  if (!el.value) return
  const blocks = el.value.querySelectorAll('pre.mermaid')
  let seq = 0
  for (const block of blocks) {
    const code = block.textContent || ''
    const id = `mmd-svg-${Date.now()}-${seq++}`
    try {
      const { svg } = await mermaid.render(id, code)
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
  <div
    ref="el"
    class="md-render overflow-auto h-full px-4 sm:px-6 py-4"
    :class="mode === 'preview' ? 'bid-doc-preview' : 'text-sm leading-relaxed'"
    v-html="html"
  />
</template>
