<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { useAiStore } from '@/stores/ai'
import { useProjectStore } from '@/stores/project'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import CheckReport from '@/components/workspace/CheckReport.vue'
import { sectionStatus } from '@/utils/project'
import { locateExcerpt } from '@/utils/sectionCheck'

const props = defineProps({
  node: { type: Object, default: null },
  projectId: { type: String, required: true },
})
const emit = defineEmits(['refs-updated'])

const ai = useAiStore()
const projectStore = useProjectStore()

const content = ref('')
// 最近一次确认已落库的正文；dirty 由二者比较得出，避免异步 watcher 与标志位赛跑
const savedContent = ref('')
const dirty = computed(() => content.value !== savedContent.value)
const viewMode = ref('edit') // edit | preview
const generating = ref(false)
const polishing = ref(false)
const customInstruction = ref('')
const abortFn = ref(null)
const retrievalMessage = ref('')
const generationMode = ref('')
const textareaRef = ref(null)

// 自动保存（防抖）；生成期间暂停——生成结果由后端在完成时写入
let saveTimer = null

function clearSaveTimer() {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = null
}

watch(
  () => props.node,
  (n) => {
    if (!n) return
    content.value = n.content || ''
    savedContent.value = content.value
    customInstruction.value = n.requirements?.join('；') || ''
  },
  { immediate: true }
)

watch(content, () => {
  clearSaveTimer()
  if (generating.value || !dirty.value) return
  saveTimer = setTimeout(autosave, 1500)
})

// 已落库的正文同步回 store：切换章节再切回时编辑器不会加载旧内容
function markSaved(nodeId, text, status) {
  savedContent.value = text
  projectStore.setSectionContent(nodeId, text, status)
}

async function autosave() {
  clearSaveTimer()
  if (!props.node || generating.value || !dirty.value) return
  const nodeId = props.node.id
  const text = content.value
  // 手写正文的待撰写章节视为已完成，计入进度
  const status = props.node.status === 'pending' && text.trim() ? 'completed' : (props.node.status || 'completed')
  try {
    await api.saveSection(props.projectId, nodeId, text, status)
    markSaved(nodeId, text, status)
  } catch (e) {
    ElMessage.error('自动保存失败：' + e.message)
  }
}

function toggleView() {
  if (viewMode.value === 'edit' && dirty.value) autosave()
  viewMode.value = viewMode.value === 'edit' ? 'preview' : 'edit'
}

async function generate() {
  if (!props.node || generating.value) return
  if (content.value.trim()) {
    try {
      await ElMessageBox.confirm(
        '重新生成将覆盖当前章节正文。生成完成后才会写入；中途停止或失败将恢复原内容。是否继续？',
        '覆盖确认',
        { type: 'warning', confirmButtonText: '重新生成', cancelButtonText: '取消' }
      )
    } catch { return }
  }
  if (dirty.value) await autosave()
  if (dirty.value) return // 保存失败时不覆盖未保存的编辑

  const original = content.value
  const node = props.node
  let finished = false
  let buffer = ''
  generating.value = true
  clearSaveTimer()
  retrievalMessage.value = ''
  generationMode.value = ''
  content.value = ''

  abortFn.value = api.streamSection(
    props.projectId,
    {
      project_id: props.projectId,
      section_id: node.id,
      section_title: node.title,
      section_path: node.path || '',
      requirements: node.requirements || [],
      custom_instruction: customInstruction.value,
      pinned_refs: node.pinned_refs || [],
      excluded_refs: node.excluded_refs || [],
    },
    (event) => {
      if (event.token) {
        buffer += event.token
        content.value = buffer
      } else if (event.refs !== undefined) {
        retrievalMessage.value = event.retrieval_message || ''
        emit('refs-updated', event.refs)
      } else if (event.done === true) {
        finished = true
        generationMode.value = event.mode || 'llm'
        ai.noteMode(event.mode)
        if (event.content !== undefined) content.value = event.content // 后端规整后的最终正文
        markSaved(node.id, content.value, event.status || 'completed') // 后端已保存
        if (retrievalMessage.value) ElMessage.info(retrievalMessage.value)
        else ElMessage.success('章节撰写完成')
      } else if (event.error) {
        ElMessage.error('生成失败：' + event.error)
      }
    },
    (e) => {
      ElMessage.error('流式连接失败：' + e.message)
    },
    () => {
      generating.value = false
      abortFn.value = null
      // 未收到完成确认（停止/失败/断线）：后端未写入，恢复原正文
      if (!finished) content.value = original
    }
  )
}

function stopGenerate() {
  if (abortFn.value) abortFn.value()
  ElMessage.info('已停止生成，已恢复原内容')
}

async function polish() {
  if (!props.node || !content.value.trim()) {
    ElMessage.warning('章节尚无内容，请先撰写')
    return
  }
  polishing.value = true
  try {
    const res = await api.polishSection(props.projectId, {
      project_id: props.projectId,
      section_id: props.node.id,
      content: content.value,
      polish_mode: 'de_ai',
    })
    content.value = res.polished_content
    ai.noteMode(res.mode)
    markSaved(props.node.id, res.polished_content, 'completed') // 后端已保存；润色不等于校审，需用户自行标记
    ElMessage.success('降AI味润色完成' + (res.improvements?.length ? `：${res.improvements.slice(0, 2).join('；')}` : ''))
  } catch (e) {
    ElMessage.error('润色失败：' + e.message)
  } finally {
    polishing.value = false
  }
}

async function markReviewed() {
  clearSaveTimer()
  const text = content.value
  try {
    await api.saveSection(props.projectId, props.node.id, text, 'reviewed')
    markSaved(props.node.id, text, 'reviewed')
    ElMessage.success('已标记校审')
  } catch (e) {
    ElMessage.error('保存失败：' + e.message)
  }
}

// ---------- 章节检查（规则离线可用；模型评审可选） ----------
const checkOpen = ref(false)
const checking = ref(false)
const checkReport = ref(null)
const checkWithLlm = ref(false)

async function runCheck() {
  if (!props.node) return
  checkOpen.value = true
  checking.value = true
  try {
    // 检查编辑器中的当前文本（含尚未自动保存的修改），不改动正文
    checkReport.value = await api.checkSection(props.projectId, props.node.id, content.value, checkWithLlm.value)
    if (checkReport.value.mode === 'llm') ai.noteMode('llm')
  } catch (e) {
    ElMessage.error('检查失败：' + e.message)
  } finally {
    checking.value = false
  }
}

function locate(item) {
  const range = locateExcerpt(content.value, item.excerpt, item.line)
  if (!range) {
    ElMessage.info('正文已修改，未找到该片段')
    return
  }
  viewMode.value = 'edit'
  checkOpen.value = false
  setTimeout(() => {
    const el = textareaRef.value
    if (!el) return
    el.focus()
    el.setSelectionRange(range[0], range[1])
    // 按选中位置大致滚动到可见区域
    const ratio = range[0] / Math.max(1, content.value.length)
    el.scrollTop = Math.max(0, ratio * el.scrollHeight - el.clientHeight / 3)
  }, 0)
}

// ---------- 历史版本 ----------
const VERSION_SOURCE = { manual: '人工稿（覆盖前快照）', ai_generate: 'AI 撰写', polish: '降AI味润色', batch: '批量撰写', restore: '恢复' }
const versionsOpen = ref(false)
const versions = ref([])
const versionPreview = ref(null)
const loadingVersions = ref(false)

async function openVersions() {
  if (!props.node) return
  if (dirty.value) await autosave()
  versionsOpen.value = true
  versionPreview.value = null
  loadingVersions.value = true
  try {
    versions.value = (await api.listVersions(props.projectId, props.node.id)).versions
  } catch (e) {
    ElMessage.error('历史版本加载失败：' + e.message)
  } finally {
    loadingVersions.value = false
  }
}

async function previewVersion(v) {
  try {
    versionPreview.value = await api.getVersion(props.projectId, props.node.id, v.id)
  } catch (e) {
    ElMessage.error('版本读取失败：' + e.message)
  }
}

async function restoreVersion(v) {
  try {
    await ElMessageBox.confirm(`将正文恢复为 ${v.created_at} 的版本（当前正文会自动留存为一个历史版本）？`, '恢复确认', { type: 'warning' })
  } catch { return }
  try {
    const res = await api.restoreVersion(props.projectId, props.node.id, v.id)
    clearSaveTimer()
    content.value = res.content
    markSaved(props.node.id, res.content, res.node_status || 'completed')
    versionsOpen.value = false
    ElMessage.success('已恢复历史版本')
  } catch (e) {
    ElMessage.error('恢复失败：' + e.message)
  }
}

const wordCount = computed(() => content.value.replace(/\s/g, '').length)
const budgetHint = computed(() => {
  if (!props.node?.word_budget) return ''
  const diff = wordCount.value - props.node.word_budget
  if (diff > props.node.word_budget * 0.15) return `超预算 ${diff} 字`
  if (diff < -props.node.word_budget * 0.15) return `距目标还差 ${-diff} 字`
  return '字数达标'
})
// 字数进度条：达标为绿、超出为黄，其余为操作色
const budgetMeter = computed(() => {
  const budget = props.node?.word_budget
  if (!budget) return null
  const diff = wordCount.value - budget
  const tone = diff > budget * 0.15 ? 'bg-warn' : diff < -budget * 0.15 ? 'bg-accent' : 'bg-ok'
  return { pct: Math.min(100, Math.round((wordCount.value / budget) * 100)), tone }
})
const statusMeta = computed(() => sectionStatus(props.node?.status))

onBeforeUnmount(() => {
  if (abortFn.value) abortFn.value()
  // 切换章节前立即保存未落库的编辑，而不是丢弃防抖中的保存
  else if (dirty.value) autosave()
  clearSaveTimer()
})
</script>

<template>
  <div v-if="node" class="h-full flex flex-col">
    <!-- 标题栏 -->
    <div class="shrink-0 flex items-center gap-2 sm:gap-3 px-2 sm:px-4 h-14 border-b border-line bg-surface">
      <slot name="toolbar-start" />
      <div class="min-w-0 flex-1">
        <p v-if="node.path" class="text-2xs text-ink-3 truncate leading-4" :title="node.path">{{ node.path }}</p>
        <div class="flex items-center gap-2 min-w-0">
          <h2 class="text-sm font-semibold text-ink truncate leading-6" :title="node.title">{{ node.title }}</h2>
          <span class="chip shrink-0" :class="statusMeta.chip">{{ statusMeta.label }}</span>
        </div>
      </div>
      <div class="seg shrink-0">
        <button class="seg-item" :class="{ 'is-active': viewMode === 'edit' }" @click="viewMode !== 'edit' && toggleView()">编辑</button>
        <button class="seg-item" :class="{ 'is-active': viewMode === 'preview' }" @click="viewMode !== 'preview' && toggleView()">预览</button>
      </div>
      <slot name="toolbar-end" />
    </div>

    <!-- 操作栏 -->
    <div class="shrink-0 flex items-center gap-2 px-2 sm:px-4 py-2 border-b border-line bg-raised flex-wrap">
      <el-button v-if="!generating" type="primary" @click="generate">
        <el-icon class="mr-1.5"><MagicStick /></el-icon>AI 撰写本节
      </el-button>
      <el-button v-else type="danger" plain @click="stopGenerate">
        <el-icon class="mr-1.5"><VideoPause /></el-icon>停止生成
      </el-button>
      <el-tooltip content="去除套话与模板腔；覆盖前自动留版，改写后仍需人工校审" placement="bottom" :show-after="400">
        <el-button text :loading="polishing" :disabled="generating || viewMode !== 'edit'" @click="polish">
          <el-icon class="sm:mr-1.5"><Brush /></el-icon><span class="hidden sm:inline">降 AI 味</span>
        </el-button>
      </el-tooltip>
      <el-tooltip content="规则检查：评分要点、承诺数值与全局事实是否一致、示例/待核实资料、篇幅与套话；不改动正文" placement="bottom" :show-after="400">
        <el-button text :disabled="generating" @click="runCheck">
          <el-icon class="sm:mr-1.5"><DocumentChecked /></el-icon><span class="hidden sm:inline">检查本章</span>
        </el-button>
      </el-tooltip>
      <el-button text title="历史版本" :disabled="generating" @click="openVersions">
        <el-icon class="sm:mr-1.5"><Clock /></el-icon><span class="hidden sm:inline">历史</span>
      </el-button>
      <el-button text title="标记校审" :type="node.status === 'reviewed' ? 'success' : ''" :disabled="!content || generating || node.status === 'reviewed'" @click="markReviewed">
        <el-icon class="sm:mr-1.5"><Select /></el-icon><span class="hidden sm:inline">{{ node.status === 'reviewed' ? '已校审' : '标记校审' }}</span>
      </el-button>

      <span class="flex-1" />
      <span v-if="generating" class="inline-flex items-center gap-1.5 text-xs text-accent-fg font-medium">
        <span class="dot bg-accent animate-pulse" />正在撰写…
      </span>
      <span v-if="generationMode === 'mock' || (ai.isMockMode && content)" class="chip chip-warn shrink-0">演示内容</span>
    </div>

    <!-- 编辑 / 预览 -->
    <div class="flex-1 min-h-0 doc-desk">
      <div v-if="viewMode === 'edit'" class="h-full max-w-[880px] mx-auto px-2 py-3 sm:px-6 sm:py-6">
        <div
          class="h-full flex flex-col bg-surface rounded-lg border shadow-sheet transition-colors"
          :class="generating ? 'border-accent/40' : 'border-line'"
        >
          <textarea
            ref="textareaRef"
            v-model="content"
            :readonly="generating"
            class="flex-1 w-full resize-none outline-none bg-transparent px-5 py-5 sm:px-10 sm:py-8 text-[15px] leading-[1.95] text-ink placeholder:text-ink-3 rounded-lg"
            placeholder="点击上方「AI 撰写本节」，结合评分要点、全局事实与知识库生成正文；也可以直接在此书写，内容会自动保存。支持 Markdown：## 小标题、- 列表、| 表格 |、```mermaid 架构图。"
          />
        </div>
      </div>
      <MarkdownPreview v-else :content="content" mode="preview" />
    </div>

    <!-- 状态栏 -->
    <div class="shrink-0 h-8 flex items-center gap-3 px-3 sm:px-4 border-t border-line bg-surface text-2xs text-ink-3">
      <span class="inline-flex items-center gap-1.5 shrink-0" :class="dirty ? 'text-warn' : ''">
        <span class="dot" :class="dirty ? 'bg-warn' : content ? 'bg-ok' : 'bg-line-strong'" />
        {{ dirty ? '未保存' : content ? '已保存' : '空白' }}
      </span>
      <span v-if="retrievalMessage && !generating" class="truncate min-w-0" :title="retrievalMessage">{{ retrievalMessage }}</span>
      <span class="flex-1" />
      <template v-if="budgetMeter">
        <span class="hidden sm:inline shrink-0">{{ budgetHint }}</span>
        <span class="num shrink-0"><b class="text-ink-2 font-semibold">{{ wordCount }}</b> / {{ node.word_budget }} 字</span>
        <span class="meter w-20 shrink-0" :title="budgetHint"><span :class="budgetMeter.tone" :style="{ width: `${budgetMeter.pct}%` }" /></span>
      </template>
      <span v-else class="num shrink-0">{{ wordCount }} 字</span>
    </div>

    <el-drawer v-model="checkOpen" :title="`检查本章 · ${node.title}`" size="min(520px, 94vw)">
      <div v-loading="checking" class="min-h-[120px]">
        <div class="flex items-center justify-between gap-2 mb-4">
          <el-tooltip :content="ai.llmConfigured ? '另请模型找出无依据的企业事实声明（一次模型调用，结果仅供参考）' : '需要先配置大模型'" placement="bottom">
            <el-checkbox v-model="checkWithLlm" :disabled="!ai.llmConfigured" size="small">含模型评审</el-checkbox>
          </el-tooltip>
          <el-button size="small" :loading="checking" @click="runCheck">重新检查</el-button>
        </div>
        <CheckReport :report="checkReport" locatable @locate="locate" />
      </div>
    </el-drawer>

    <el-drawer v-model="versionsOpen" :title="`历史版本 · ${node.title}`" size="min(600px, 94vw)">
      <div v-loading="loadingVersions" class="space-y-2">
        <p v-if="!versions.length && !loadingVersions" class="text-xs text-ink-3 py-10 text-center leading-relaxed">
          暂无历史版本。<br />AI 撰写、润色、批量撰写或恢复覆盖正文时会自动留存版本（含覆盖前的人工稿）。
        </p>
        <button
          v-for="v in versions"
          :key="v.id"
          class="w-full text-left rounded-lg border px-3 py-2.5 transition"
          :class="versionPreview?.id === v.id ? 'border-accent bg-accent-soft/50' : 'border-line hover:border-line-strong'"
          @click="previewVersion(v)"
        >
          <div class="flex items-center justify-between gap-2 text-xs">
            <span class="font-medium text-ink">{{ VERSION_SOURCE[v.source] || v.source }}</span>
            <span class="text-ink-3 num">{{ v.created_at }} · {{ v.char_count }} 字</span>
          </div>
          <p class="text-xs text-ink-2 mt-1 line-clamp-2 leading-relaxed">{{ v.preview }}</p>
        </button>
        <div v-if="versionPreview" class="pt-4 mt-2">
          <div class="flex items-center justify-between mb-3">
            <span class="text-xs font-semibold text-ink">版本预览</span>
            <el-button size="small" type="primary" @click="restoreVersion(versionPreview)">恢复此版本</el-button>
          </div>
          <div class="rounded-lg overflow-hidden border border-line doc-desk">
            <MarkdownPreview :content="versionPreview.content" mode="preview" dense />
          </div>
        </div>
      </div>
    </el-drawer>
  </div>

  <div v-else class="h-full flex flex-col">
    <div class="shrink-0 flex items-center gap-2 px-2 sm:px-4 h-14 border-b border-line bg-surface">
      <slot name="toolbar-start" />
      <span class="flex-1" />
      <slot name="toolbar-end" />
    </div>
    <div class="flex-1 doc-desk flex items-center justify-center">
      <EmptyState icon="Pointer" title="从左侧大纲选择章节开始撰写" description="叶子章节可直接撰写，有子节的章节也可写一段整章综述。" />
    </div>
  </div>
</template>
