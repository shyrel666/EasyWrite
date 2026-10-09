<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { useAiStore } from '@/stores/ai'
import { useProjectStore } from '@/stores/project'
import { useTaskStore } from '@/stores/tasks'
import { useSectionSave } from '@/composables/useSectionSave'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import CheckReport from '@/components/workspace/CheckReport.vue'
import ProposalPanel from '@/components/workspace/ProposalPanel.vue'
import RefinePanel from '@/components/workspace/RefinePanel.vue'
import { sectionStatus } from '@/utils/project'
import { isActiveTask } from '@/utils/tasks'
import { DEFAULT_MAX_ROUNDS, MAX_ROUNDS_LIMIT, canResume, outcomeMeta, stopReasonLabel } from '@/utils/refine'
import { locateExcerpt } from '@/utils/sectionCheck'

const props = defineProps({
  node: { type: Object, default: null },
  projectId: { type: String, required: true },
})
const emit = defineEmits(['refs-updated', 'proposals-changed'])

const ai = useAiStore()
const projectStore = useProjectStore()
const taskStore = useTaskStore()

const viewMode = ref('edit') // edit | preview
const generating = ref(false)
const polishing = ref(false)
const customInstruction = ref('')
const abortFn = ref(null)
const retrievalMessage = ref('')
const generationMode = ref('')
const textareaRef = ref(null)

// 正文与保存状态：防抖自动保存（生成期间暂停，生成结果由后端在完成时写入）、串行保存、过期响应按修订号丢弃
const {
  content, savedRevision, dirty,
  syncNode, markSaved, isCurrentRevision, autosave, flushSave, saveOnLeave, clearSaveTimer, editMark, editedSince,
} = useSectionSave({ node: () => props.node, projectId: () => props.projectId, paused: generating })

watch(
  () => props.node,
  (n, previous) => {
    if (syncNode(n, previous)) customInstruction.value = n.requirements?.join('；') || ''
  },
  { immediate: true }
)

defineExpose({ flushSave })

function toggleView() {
  if (viewMode.value === 'edit' && dirty.value) autosave()
  viewMode.value = viewMode.value === 'edit' ? 'preview' : 'edit'
}

async function generate() {
  if (!props.node || generating.value || polishing.value) return
  if (content.value.trim()) {
    try {
      await ElMessageBox.confirm(
        '重新生成将覆盖当前章节正文。生成完成后才会写入；中途停止或失败将恢复原内容。是否继续？',
        '覆盖确认',
        { type: 'warning', confirmButtonText: '重新生成', cancelButtonText: '取消' }
      )
    } catch { return }
  }
  if (!await flushSave()) return

  const original = content.value
  let conflict = null
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
      base_revision: savedRevision.value,
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
        markSaved(node.id, content.value, event.status || 'completed', event.revision) // 后端已保存
        if (retrievalMessage.value) ElMessage.info(retrievalMessage.value)
        else ElMessage.success('章节撰写完成')
      } else if (event.error) {
        if (event.status_code === 409) {
          conflict = event
          emit('proposals-changed')
        }
        ElMessage.error('生成失败：' + event.error)
      }
    },
    (e) => {
      if (e.status === 409 && e.detail?.current_content !== undefined) {
        conflict = e.detail
        emit('proposals-changed')
      }
      ElMessage.error('流式连接失败：' + e.message)
    },
    () => {
      generating.value = false
      abortFn.value = null
      // 未收到完成确认（停止/失败/断线）：后端未写入，恢复原正文
      if (!finished) {
        content.value = conflict?.current_content ?? original
        if (conflict) markSaved(node.id, content.value, conflict.current_status, conflict.current_revision)
      }
    }
  )
}

function stopGenerate() {
  if (abortFn.value) abortFn.value()
  ElMessage.info('已停止生成，已恢复原内容')
}

async function polish() {
  if (polishing.value || generating.value) return
  if (!props.node || !content.value.trim()) {
    ElMessage.warning('章节尚无内容，请先撰写')
    return
  }
  polishing.value = true
  const nodeId = props.node.id
  let mark
  try {
    if (!await flushSave()) return
    const original = content.value
    mark = editMark()
    const res = await api.polishSection(props.projectId, {
      project_id: props.projectId,
      section_id: nodeId,
      content: original,
      base_revision: savedRevision.value,
      polish_mode: 'de_ai',
    })
    const edited = editedSince(mark)
    // 润色期间允许继续输入；即使新输入还在防抖窗口，也不能用 AI 结果替换它。
    const currentResult = isCurrentRevision(nodeId, res.revision)
    if (props.node?.id === nodeId && !edited && currentResult) content.value = res.polished_content
    ai.noteMode(res.mode)
    markSaved(nodeId, res.polished_content, 'completed', res.revision)
    // 服务端写入润色稿时已把它存为历史版本；随后保存的人工正文覆盖它也能从历史版本找回
    if (edited || !currentResult) ElMessage.info('润色期间正文有改动，已保留你的正文；润色结果可在「历史版本」中找回')
    else ElMessage.success('降AI味润色完成' + (res.improvements?.length ? `：${res.improvements.slice(0, 2).join('；')}` : ''))
  } catch (e) {
    if (e.status === 409) {
      const current = e.detail
      if (current?.current_content !== undefined && props.node?.id === nodeId
          && isCurrentRevision(nodeId, current.current_revision)) {
        if (!editedSince(mark)) content.value = current.current_content
        markSaved(nodeId, current.current_content, current.current_status, current.current_revision)
      }
      emit('proposals-changed')
    }
    ElMessage.error('润色失败：' + e.message)
  } finally {
    polishing.value = false
  }
}

async function markReviewed() {
  if (!await flushSave()) return
  const text = content.value
  try {
    const res = await api.saveSection(props.projectId, props.node.id, text, 'reviewed')
    markSaved(props.node.id, text, 'reviewed', res.revision)
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

// ---------- 候选稿：AI 对已有正文的改动，查看差异后采纳 ----------
const proposalOpen = ref(false)
const proposalId = ref('')
const proposalTask = ref('')
const pendingProposals = computed(() => projectStore.proposalCounts[props.node?.id] || 0)

async function openProposals() {
  if (!props.node) return
  await flushSave()
  try {
    const { items } = await api.sectionProposals(props.projectId, props.node.id)
    const open = items.find((p) => ['draft', 'checked'].includes(p.status))
    if (!open) {
      ElMessage.info('本节没有待处理的候选稿')
      emit('proposals-changed')
      return
    }
    proposalTask.value = ''
    proposalId.value = open.id
    proposalOpen.value = true
  } catch (e) {
    ElMessage.error('候选稿加载失败：' + e.message)
  }
}

// 按检查结果定向修订：先保存当前正文，修订结果是候选稿，不直接写入
async function reviseFromCheck() {
  if (!props.node) return
  if (!await flushSave()) return
  try {
    const res = await api.reviseSection(props.projectId, props.node.id)
    checkOpen.value = false
    proposalId.value = ''
    proposalTask.value = res.task_id
    proposalOpen.value = true
  } catch (e) {
    ElMessage.warning(e.message)
  }
}

function onProposalApplied({ content: text, status, revision }) {
  clearSaveTimer()
  content.value = text
  markSaved(props.node.id, text, status, revision)
}

// ---------- 智能完善：起草或以当前正文为原稿 → 检查 → 定向修订，产出带检查报告的候选稿 ----------
const refineDialog = ref(false)
const refineForm = reactive({ instruction: '', maxRounds: DEFAULT_MAX_ROUNDS, applyIfBlank: false })
const refineOpen = ref(false)
const refineTask = ref(null) // 本节最近一次智能完善任务（进行中的由此处轮询，面板只负责展示）
const refineStarting = ref(false)
const refineActive = computed(() => isActiveTask(refineTask.value))
let refineAbort = null

function stopRefinePoll() {
  if (refineAbort) refineAbort.abort()
  refineAbort = null
}

function followRefine(taskId, sid) {
  stopRefinePoll()
  const controller = new AbortController()
  refineAbort = controller
  api.pollTask(taskId, (t) => { if (props.node?.id === sid) refineTask.value = t }, { signal: controller.signal })
    .then((final) => {
      if (props.node?.id !== sid) return
      refineTask.value = final
      onRefineFinished(final)
    })
    .catch(() => { /* 切换章节或卸载时中止轮询 */ })
}

// 切换章节时找回本节最近一次智能完善（进行中的继续跟进度，中断或预算用尽的可以继续）
async function loadRefineState() {
  stopRefinePoll()
  refineTask.value = null
  refineOpen.value = false
  const sid = props.node?.id
  if (!sid) return
  try {
    const { tasks } = await api.listTasks({ projectId: props.projectId, type: 'section_refine', withResult: true, limit: 20 })
    if (props.node?.id !== sid) return
    const task = tasks.find((t) => t.meta?.section_id === sid) || null
    refineTask.value = task
    if (isActiveTask(task)) followRefine(task.id, sid)
  } catch { /* 仅用于恢复显示，失败不影响编辑 */ }
}
watch(() => props.node?.id, loadRefineState, { immediate: true })

function openRefineDialog() {
  refineForm.instruction = ''
  refineForm.applyIfBlank = false
  refineDialog.value = true
}

async function startRefine({ resume = false } = {}) {
  if (!props.node || refineStarting.value) return
  if (!await flushSave()) return // 保存失败：智能完善基于已保存的正文
  const sid = props.node.id
  refineStarting.value = true
  try {
    const options = resume
      ? { resume: true }
      : { instruction: refineForm.instruction, maxRounds: refineForm.maxRounds, applyIfBlank: refineForm.applyIfBlank && !content.value.trim() }
    const res = await api.refineSection(props.projectId, sid, options)
    refineDialog.value = false
    refineTask.value = { id: res.task_id, type: 'section_refine', status: 'pending', progress: 0, message: '已提交', meta: { section_id: sid }, result: null }
    refineOpen.value = true
    followRefine(res.task_id, sid)
    if (taskStore.watchers) taskStore.refresh()
  } catch (e) {
    ElMessage.warning(e.message)
  } finally {
    refineStarting.value = false
  }
}

async function onRefineFinished(task) {
  emit('proposals-changed')
  if (taskStore.watchers) taskStore.refresh()
  if (task.status === 'failed') {
    ElMessage.error('智能完善失败：' + (task.error || task.message || '').split('\n')[0])
    return
  }
  const r = task.result
  if (!r) return
  if (r.applied && r.final_proposal_id) {
    // 空白章节达成目标后已按采纳流程写入；编辑器中有未保存的修改时不覆盖
    try {
      const p = await api.getProposal(props.projectId, r.section_id, r.final_proposal_id)
      const current = p.state
      if (props.node?.id === r.section_id && !dirty.value) onProposalApplied({ content: current.current_content, status: current.current_status, revision: current.current_revision })
      else projectStore.setSectionContent(r.section_id, current.current_content, current.current_status, current.current_revision)
    } catch { /* 正文已写入，重新打开章节可见 */ }
  }
  const meta = outcomeMeta(r.outcome)
  const reason = r.outcome === 'goal_met' ? '' : `（${stopReasonLabel(r.stop_reason)}）`
  ElMessage[r.outcome === 'goal_met' ? 'success' : 'warning'](`智能完善：${meta.label}${reason}`)
}

// 编辑器顶部提示：进行中 / 可继续
const refineBanner = computed(() => {
  const task = refineTask.value
  if (!task) return null
  if (isActiveTask(task)) return { tone: 'note-info', icon: 'Loading', text: `智能完善进行中：${task.message || '已提交'}`, action: '查看进度' }
  if (canResume(task) && pendingProposals.value) {
    const why = task.status === 'interrupted' ? '服务重启，任务已中断' : stopReasonLabel(task.result?.stop_reason)
    return { tone: 'note-warn', icon: 'Warning', text: `上次智能完善未完成（${why}），可以从最后一版候选稿继续。`, action: '查看结果', resumable: true }
  }
  return null
})

// ---------- 历史版本 ----------
const VERSION_SOURCE = { manual: '人工稿（覆盖前快照）', ai_generate: 'AI 撰写', polish: '降AI味润色', batch: '批量撰写', restore: '恢复', deviation: '偏离表回填', proposal: '采纳候选稿' }
const versionsOpen = ref(false)
const versions = ref([])
const versionPreview = ref(null)
const loadingVersions = ref(false)

async function openVersions() {
  if (!props.node) return
  await flushSave()
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
    markSaved(props.node.id, res.content, res.node_status || 'completed', res.revision)
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
  stopRefinePoll()
  if (abortFn.value) abortFn.value()
  else saveOnLeave()
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
    <div class="shrink-0 flex items-center gap-2 px-2 sm:px-4 py-2 border-b border-line bg-raised flex-wrap [&_.el-button+.el-button]:ml-0 max-sm:[&_.el-button.is-text]:px-2.5">
      <el-button v-if="!generating" type="primary" :disabled="polishing" @click="generate">
        <el-icon class="mr-1.5"><MagicStick /></el-icon>AI 撰写本节
      </el-button>
      <el-button v-else type="danger" plain @click="stopGenerate">
        <el-icon class="mr-1.5"><VideoPause /></el-icon>停止生成
      </el-button>
      <el-tooltip :content="ai.llmConfigured ? '自动起草（本节为空时）或以当前正文为原稿 → 规则检查 → 按问题定向修订，结果是带检查报告的候选稿' : '智能完善需要先配置大模型；检查本章不需要模型'" placement="bottom" :show-after="400">
        <span>
          <el-button text type="primary" :disabled="!ai.llmConfigured || generating || refineActive" @click="openRefineDialog">
            <el-icon class="sm:mr-1.5"><Aim /></el-icon><span class="hidden sm:inline">智能完善</span>
          </el-button>
        </span>
      </el-tooltip>
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

    <div v-if="pendingProposals" class="shrink-0 px-2 sm:px-4 py-2 border-b border-line bg-surface">
      <p class="note note-info !py-2 items-center">
        <el-icon class="text-accent-fg shrink-0"><DocumentCopy /></el-icon>
        <span class="flex-1">本节有 {{ pendingProposals }} 份候选稿待处理：AI 的改动经你查看差异并采纳后才会写入正文。</span>
        <el-button size="small" type="primary" plain :disabled="generating" @click="openProposals">查看候选稿</el-button>
      </p>
    </div>

    <div v-if="refineBanner" class="shrink-0 px-2 sm:px-4 py-2 border-b border-line bg-surface">
      <p class="note !py-2 items-center" :class="refineBanner.tone">
        <el-icon class="shrink-0" :class="refineBanner.icon === 'Loading' ? 'animate-spin text-accent-fg' : 'text-warn'"><component :is="refineBanner.icon" /></el-icon>
        <span class="flex-1 min-w-0 truncate" :title="refineBanner.text">{{ refineBanner.text }}</span>
        <el-button size="small" plain @click="refineOpen = true">{{ refineBanner.action }}</el-button>
        <el-button v-if="refineBanner.resumable" size="small" type="primary" plain :loading="refineStarting" @click="startRefine({ resume: true })">继续</el-button>
      </p>
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
        <div v-if="checkReport && checkReport.issues.some((i) => i.source === 'rule')" class="mt-5 pt-4 border-t border-line">
          <el-tooltip :content="ai.llmConfigured ? '按上述规则问题定向修订已保存的正文，结果是候选稿，查看差异后再采纳' : '定向修订需要先配置大模型'" placement="top">
            <span>
              <el-button size="small" :disabled="!ai.llmConfigured || generating" @click="reviseFromCheck">
                <el-icon class="mr-1"><EditPen /></el-icon>按检查结果修订（生成候选稿）
              </el-button>
            </span>
          </el-tooltip>
        </div>
      </div>
    </el-drawer>

    <el-drawer v-model="proposalOpen" :title="`候选稿 · ${node.title}`" size="min(760px, 96vw)" destroy-on-close>
      <ProposalPanel
        v-if="proposalOpen"
        :project-id="projectId"
        :section-id="node.id"
        :proposal-id="proposalId"
        :task-id="proposalTask"
        @applied="onProposalApplied"
        @changed="emit('proposals-changed')"
      />
    </el-drawer>

    <el-drawer v-model="refineOpen" :title="`智能完善 · ${node.title}`" size="min(760px, 96vw)">
      <RefinePanel
        v-if="refineOpen && refineTask"
        :project-id="projectId"
        :section-id="node.id"
        :task="refineTask"
        :resuming="refineStarting"
        @resume="startRefine({ resume: true })"
        @restart="openRefineDialog"
        @applied="onProposalApplied"
        @changed="emit('proposals-changed')"
      />
    </el-drawer>

    <el-dialog v-model="refineDialog" :title="`智能完善 · ${node.title}`" width="min(520px, 94vw)" append-to-body>
      <div class="space-y-4 text-sm">
        <p class="text-xs text-ink-2 leading-relaxed">
          {{ content.trim() ? '以当前正文为原稿' : '本节为空，先起草' }}，然后做规则检查，并按检查出的问题定向修订。每一版都是带检查报告的候选稿，查看差异后再采纳；资料不足的内容用【待填写】【待核实】占位，不会补写。
        </p>
        <div>
          <p class="field-label">补充要求（可选）</p>
          <el-input v-model="refineForm.instruction" type="textarea" :rows="3" maxlength="500" show-word-limit placeholder="如：补充驻场安排；删除未经确认的人员姓名" />
        </div>
        <div>
          <p class="field-label">修订轮数</p>
          <div class="seg">
            <button v-for="n in MAX_ROUNDS_LIMIT + 1" :key="n" class="seg-item" :class="{ 'is-active': refineForm.maxRounds === n - 1 }" @click="refineForm.maxRounds = n - 1">
              {{ n - 1 }} 轮
            </button>
          </div>
          <p class="hint mt-1.5">不含首次起草。达成检查目标即停止；连续两轮没有进展会改为只处理阻塞问题。单次运行有模型请求次数与时长上限，超出时保留已有候选稿，可以继续。</p>
        </div>
        <el-checkbox v-if="!content.trim()" v-model="refineForm.applyIfBlank" class="!h-auto !whitespace-normal">达成检查目标后直接写入正文（经候选稿采纳流程）</el-checkbox>
      </div>
      <template #footer>
        <el-button @click="refineDialog = false">取消</el-button>
        <el-button type="primary" :loading="refineStarting" @click="startRefine()">开始</el-button>
      </template>
    </el-dialog>

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
