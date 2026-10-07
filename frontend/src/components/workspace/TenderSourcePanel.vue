<script setup>
import { computed, ref, watch } from 'vue'
import api from '@/api/client'
import { formatChars, highlightSegments } from '@/utils/highlight'

// 招标原文：浏览章节树、分段阅读，高亮当前撰写章节的要点
const props = defineProps({
  projectId: { type: String, required: true },
  node: { type: Object, default: null },
})

const PAGE = 20000
const loading = ref(false)
const error = ref('')
const hasStructure = ref(true)
const sections = ref([])
const keywords = ref([])
const related = ref([])
const expanded = ref(new Set())
const filter = ref('')
const reading = ref(null)
const readingLoading = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.tenderOutline(props.projectId, props.node?.id)
    hasStructure.value = res.has_structure
    sections.value = res.sections
    keywords.value = res.keywords
    related.value = res.related
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

watch(() => props.node?.id, () => {
  reading.value = null
  load()
}, { immediate: true })

// 章节树展开为带缩进的行；有筛选词时只显示标题命中的章节（附完整路径）
const rows = computed(() => {
  const kw = filter.value.trim()
  const out = []
  const walk = (nodes, depth) => {
    for (const n of nodes) {
      const open = expanded.value.has(n.id)
      if (!kw) out.push({ ...n, depth, open })
      else if (n.title.includes(kw)) out.push({ ...n, depth: 0, open: false, showPath: true })
      if (n.children.length && (kw || open)) walk(n.children, depth + 1)
    }
  }
  walk(sections.value, 0)
  return out
})

function toggle(row) {
  const next = new Set(expanded.value)
  if (next.has(row.id)) next.delete(row.id)
  else next.add(row.id)
  expanded.value = next
}

async function read(id) {
  readingLoading.value = true
  try {
    reading.value = await api.tenderSection(props.projectId, id, 0, PAGE)
  } catch (e) {
    error.value = e.message
  } finally {
    readingLoading.value = false
  }
}

async function readMore() {
  if (!reading.value) return
  readingLoading.value = true
  try {
    const next = await api.tenderSection(props.projectId, reading.value.id, reading.value.end, PAGE)
    reading.value = { ...next, text: reading.value.text + next.text, start: 0 }
  } catch (e) {
    error.value = e.message
  } finally {
    readingLoading.value = false
  }
}

// 阅读视图按行渲染："## 标题"行为下级章节标题，其余为正文（含表格行）
const lines = computed(() =>
  (reading.value?.text || '').split('\n').filter((l) => l.trim()).map((l) =>
    l.startsWith('## ')
      ? { heading: true, parts: highlightSegments(l.slice(3), keywords.value) }
      : { heading: false, parts: highlightSegments(l, keywords.value) }
  )
)
const hitCount = computed(() => lines.value.reduce((n, l) => n + l.parts.filter((p) => p.hit).length, 0))
</script>

<template>
  <div class="text-xs">
    <p v-if="error" class="note note-bad mb-3">{{ error }}</p>

    <!-- 阅读 -->
    <template v-if="reading">
      <div class="flex items-start gap-1.5 mb-2">
        <button class="icon-btn !w-7 !h-7 shrink-0 -ml-1" title="返回章节列表" @click="reading = null"><el-icon><ArrowLeft /></el-icon></button>
        <div class="min-w-0 pt-1">
          <p class="font-semibold text-ink leading-snug">{{ reading.title }}</p>
          <p class="text-2xs text-ink-3 mt-0.5 break-all">{{ reading.path }}</p>
        </div>
      </div>
      <p v-if="keywords.length" class="hint mb-2">
        <template v-if="hitCount">高亮 <span class="num">{{ hitCount }}</span> 处本节要点</template>
        <template v-else>本章节未出现本节要点</template>
      </p>
      <div class="rounded-lg border border-line bg-raised px-3 py-2.5 leading-relaxed text-ink-2 space-y-1.5">
        <p v-if="!lines.length" class="text-ink-3">本章节没有正文（请查看下级章节）。</p>
        <p v-for="(l, i) in lines" :key="i" :class="l.heading ? 'font-semibold text-ink pt-2' : 'break-words'">
          <template v-for="(part, j) in l.parts" :key="j">
            <mark v-if="part.hit" class="bg-warn/25 text-ink rounded-[2px] px-px">{{ part.text }}</mark>
            <template v-else>{{ part.text }}</template>
          </template>
        </p>
      </div>
      <div v-if="reading.end < reading.total" class="mt-2 flex items-center justify-between">
        <span class="text-2xs text-ink-3 num">已显示 {{ formatChars(reading.end) }} / {{ formatChars(reading.total) }} 字</span>
        <el-button size="small" :loading="readingLoading" @click="readMore">继续阅读</el-button>
      </div>
    </template>

    <!-- 章节列表 -->
    <template v-else>
      <div v-if="loading" class="py-10 text-center text-ink-3">加载招标文件章节…</div>
      <div v-else-if="!hasStructure" class="text-ink-3 py-12 px-4 text-center leading-relaxed">
        <el-icon :size="22" class="text-line-strong mb-2"><Document /></el-icon>
        <p>本项目没有存档的招标文件章节。</p>
        <p class="mt-1">在项目向导中上传招标文件（.docx / .pdf）后，可在此按章节阅读原文。</p>
      </div>
      <template v-else>
        <section v-if="keywords.length" class="mb-3">
          <p class="field-label">本节要点</p>
          <div class="flex flex-wrap gap-1">
            <span v-for="k in keywords" :key="k" class="chip chip-accent max-w-full truncate">{{ k }}</span>
          </div>
        </section>
        <section v-if="related.length" class="mb-3">
          <p class="field-label">要点出现的章节</p>
          <button
            v-for="r in related"
            :key="r.id"
            class="w-full text-left rounded-lg border border-line hover:border-line-strong px-2.5 py-2 mb-1.5 transition"
            @click="read(r.id)"
          >
            <span class="block text-ink font-medium leading-snug">{{ r.title }}</span>
            <span class="block text-2xs text-ink-3 truncate mt-0.5" :title="r.path">{{ r.path }}</span>
            <span class="block text-2xs text-accent-fg mt-1 truncate">命中：{{ r.hits.join('、') }}</span>
          </button>
        </section>
        <p v-else-if="keywords.length" class="hint mb-3">本节要点未在招标文件中原样出现，可在下方章节树中查找。</p>

        <div class="flex items-center justify-between gap-2 mb-1.5">
          <p class="field-label !mb-0">全部章节</p>
          <el-input v-model="filter" size="small" clearable placeholder="按标题筛选" class="!w-36" />
        </div>
        <div class="space-y-px">
          <div
            v-for="row in rows"
            :key="row.id"
            class="flex items-center gap-1 rounded-md hover:bg-sunken pr-1.5"
            :style="{ paddingLeft: `${row.depth * 12}px` }"
          >
            <button
              v-if="row.children.length && !filter"
              class="w-5 h-6 shrink-0 flex items-center justify-center text-ink-3"
              :title="row.open ? '收起' : '展开'"
              @click="toggle(row)"
            >
              <el-icon :class="{ 'rotate-90': row.open }" class="transition-transform"><ArrowRight /></el-icon>
            </button>
            <span v-else class="w-5 shrink-0" />
            <button class="flex-1 min-w-0 text-left py-1" @click="read(row.id)">
              <span class="block truncate text-ink-2 hover:text-ink" :title="row.path">{{ row.title }}</span>
              <span v-if="row.showPath" class="block text-2xs text-ink-3 truncate">{{ row.path }}</span>
            </button>
            <span class="text-2xs text-ink-3 num shrink-0">{{ formatChars(row.total_chars) }}</span>
          </div>
          <p v-if="!rows.length" class="text-ink-3 py-4 text-center">没有匹配的章节</p>
        </div>
      </template>
    </template>
  </div>
</template>
