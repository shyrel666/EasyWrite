<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { useAiStore } from '@/stores/ai'
import CheckReport from '@/components/workspace/CheckReport.vue'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import TaskProgress from '@/components/common/TaskProgress.vue'
import { collapseRows, diffText } from '@/utils/textDiff'
import { VERIFY_KIND } from '@/utils/sectionCheck'
import { proposalOriginLabel } from '@/utils/refine'

/**
 * 候选稿面板：正文差异、检查报告、所用资料、待核实事项；采纳 / 放弃 / 基于当前正文重新修订。
 * 传入 taskId 时先显示修订任务进度，完成后载入任务产出的候选稿。
 */
const props = defineProps({
  projectId: { type: String, required: true },
  sectionId: { type: String, required: true },
  proposalId: { type: String, default: '' },
  taskId: { type: String, default: '' },
})
// applied：采纳成功（{content, status}）；changed：候选稿状态有变化（刷新大纲标记）
const emit = defineEmits(['applied', 'changed'])

const ai = useAiStore()
const currentId = ref(props.proposalId)
const reviseTaskId = ref(props.taskId)
const detail = ref(null)
const loading = ref(false)
const acting = ref('')
const conflict = ref(null)
const tab = ref('diff')
const diffView = ref('changes') // changes 只看改动 | all 全部 | full 候选稿全文

const STATUS = {
  draft: { label: '未检查', chip: 'chip-mute' },
  checked: { label: '待采纳', chip: 'chip-accent' },
  applied: { label: '已采纳', chip: 'chip-ok' },
  rejected: { label: '已放弃', chip: 'chip-mute' },
  superseded: { label: '已被取代', chip: 'chip-mute' },
}
const CONFLICT_HINT = {
  content_changed: '可以基于当前正文重新修订，或放弃此候选稿。',
  basis_changed: '候选稿是按旧依据写的，请基于当前正文和最新依据重新修订，或放弃。',
}
const ASSET_KIND = { qualifications: '资质', personnel: '人员', cases: '业绩', components: '方案组件' }
const ASSET_STATUS = { confirmed: { label: '已确认', chip: 'chip-ok' }, unverified: { label: '待核实', chip: 'chip-warn' } }

async function load() {
  if (!currentId.value) return
  loading.value = true
  try {
    detail.value = await api.getProposal(props.projectId, props.sectionId, currentId.value)
  } catch (e) {
    ElMessage.error('候选稿加载失败：' + e.message)
  } finally {
    loading.value = false
  }
}

watch(() => [props.proposalId, props.taskId], ([pid, tid]) => {
  currentId.value = pid
  reviseTaskId.value = tid
  conflict.value = null
  detail.value = null
  load()
}, { immediate: true })

const isOpen = computed(() => ['draft', 'checked'].includes(detail.value?.status))
const state = computed(() => detail.value?.state || {})
// 已知正文或依据变化时采纳必然被拒绝：直接禁用并说明（并发变化仍由服务端 409 兜底）
const staleReason = computed(() => {
  if (state.value.content_changed) return '本节正文在候选稿生成后被修改过，请重新修订或放弃'
  if (state.value.basis_changes?.length) return '依据已变化，请重新修订或放弃'
  return ''
})
const diff = computed(() => (detail.value ? diffText(state.value.current_content || '', detail.value.content) : null))
const diffRows = computed(() => {
  if (!diff.value) return []
  return diffView.value === 'changes' ? collapseRows(diff.value.rows, 2) : diff.value.rows
})
const assets = computed(() => (detail.value?.evidence || []).filter((r) => r.ref_type === 'asset'))
const excludedAssets = computed(() => (detail.value?.evidence || []).filter((r) => r.ref_type === 'asset_excluded'))
const kbRefs = computed(() => (detail.value?.evidence || []).filter((r) => !r.ref_type || r.ref_type === 'kb'))
const verifyItems = computed(() => detail.value?.report?.pending_verification || [])
const report = computed(() => detail.value?.report || null)

async function apply() {
  if (!detail.value) return
  if (report.value?.blocking_count) {
    try {
      await ElMessageBox.confirm(
        `检查发现 ${report.value.blocking_count} 个阻塞问题（见"检查报告"）。采纳后需要在正文中处理这些问题，是否仍要采纳？`,
        '采纳确认', { type: 'warning', confirmButtonText: '仍要采纳', cancelButtonText: '取消' },
      )
    } catch { return }
  }
  acting.value = 'apply'
  conflict.value = null
  try {
    const res = await api.applyProposal(props.projectId, props.sectionId, detail.value.id)
    emit('applied', { content: res.content, status: res.node_status || 'completed' })
    emit('changed')
    ElMessage.success(res.already_applied ? '该候选稿此前已采纳' : '已采纳候选稿，原正文已留存为历史版本')
    await load()
  } catch (e) {
    if (e.status === 409 && e.detail && typeof e.detail === 'object') {
      conflict.value = e.detail // 不静默覆盖：说明原因，给出可选操作
      await load()
    } else {
      ElMessage.error('采纳失败：' + e.message)
    }
  } finally {
    acting.value = ''
  }
}

async function reject() {
  if (!detail.value) return
  acting.value = 'reject'
  try {
    await api.rejectProposal(props.projectId, props.sectionId, detail.value.id)
    ElMessage.info('已放弃候选稿，正文保持不变')
    conflict.value = null
    emit('changed')
    await load()
  } catch (e) {
    ElMessage.error('操作失败：' + e.message)
  } finally {
    acting.value = ''
  }
}

async function revise() {
  let instruction = ''
  try {
    const { value } = await ElMessageBox.prompt(
      '按当前正文的检查问题定向修订，结果生成新的候选稿（不直接写入正文）。可补充修订要求：',
      '基于当前正文重新修订',
      { inputPlaceholder: '可留空，如：补充驻场安排，删除未经确认的人员姓名', confirmButtonText: '开始修订', cancelButtonText: '取消' },
    )
    instruction = value || ''
  } catch { return }
  acting.value = 'revise'
  try {
    const res = await api.reviseSection(props.projectId, props.sectionId, { parentId: currentId.value, instruction })
    conflict.value = null
    reviseTaskId.value = res.task_id
  } catch (e) {
    ElMessage.warning(e.message)
  } finally {
    acting.value = ''
  }
}

async function onReviseDone(task) {
  reviseTaskId.value = ''
  const result = task.result || {}
  if (task.status !== 'completed' || !result.proposal_id) {
    ElMessage[task.status === 'completed' ? 'info' : 'warning'](result.message || (task.status === 'cancelled' ? '已取消修订' : '修订未生成候选稿'))
    await load()
    return
  }
  currentId.value = result.proposal_id
  tab.value = 'diff'
  emit('changed')
  await load()
}

function onReviseFailed(task) {
  reviseTaskId.value = ''
  ElMessage.error('修订失败：' + (task.error || task.message || '未知错误'))
  load()
}
</script>

<template>
  <div class="h-full flex flex-col text-sm">
    <TaskProgress
      v-if="reviseTaskId"
      :task-id="reviseTaskId"
      title="正在定向修订"
      cancellable
      class="mb-4"
      @done="onReviseDone"
      @failed="onReviseFailed"
    />

    <div v-if="loading && !detail" v-loading="true" class="h-40" />
    <p v-else-if="!detail && !reviseTaskId" class="text-xs text-ink-3 py-10 text-center">没有候选稿</p>

    <template v-if="detail">
      <!-- 概要 -->
      <div class="shrink-0 flex flex-wrap items-center gap-2 text-xs">
        <span class="chip" :class="STATUS[detail.status]?.chip">{{ STATUS[detail.status]?.label || detail.status }}</span>
        <span class="text-ink-2">{{ proposalOriginLabel(detail) }}</span>
        <span class="text-ink-3 num">{{ detail.created_at }} · {{ detail.char_count }} 字</span>
        <span v-if="diff" class="num ml-auto">
          <span class="text-ok">+{{ diff.stats.added }}</span>
          <span class="text-bad ml-1.5">−{{ diff.stats.removed }}</span>
        </span>
      </div>

      <div class="shrink-0 space-y-2 mt-3">
        <p v-if="conflict" class="note note-bad">
          <el-icon class="mt-0.5 text-bad shrink-0"><WarningFilled /></el-icon>
          <span><b>未采纳：</b>{{ conflict.message }}。{{ CONFLICT_HINT[conflict.reason] || '' }}</span>
        </p>
        <template v-else-if="isOpen">
          <p v-if="state.content_changed" class="note note-warn">
            <el-icon class="mt-0.5 text-warn shrink-0"><Warning /></el-icon>
            <span>{{ state.section_exists === false ? '章节已被删除。' : '候选稿生成后，本节正文被修改过：采纳会被拒绝。下方差异按当前正文计算。' }}</span>
          </p>
          <p v-if="state.basis_changes?.length" class="note note-warn">
            <el-icon class="mt-0.5 text-warn shrink-0"><Warning /></el-icon>
            <span>依据已变化：{{ state.basis_changes.map((c) => c.label).join('、') }}。采纳会被拒绝，请重新修订。</span>
          </p>
        </template>
      </div>

      <!-- 分栏 -->
      <div class="shrink-0 seg mt-3 self-start max-w-full overflow-x-auto">
        <button v-for="t in [
          { k: 'diff', l: '正文差异' },
          { k: 'report', l: '检查报告', n: report ? report.blocking_count + report.quality_count : null },
          { k: 'assets', l: '所用资料', n: assets.length + excludedAssets.length + kbRefs.length },
          { k: 'verify', l: '待核实', n: verifyItems.length },
        ]" :key="t.k" class="seg-item" :class="{ 'is-active': tab === t.k }" @click="tab = t.k">
          {{ t.l }}<span v-if="t.n" class="ml-1 num" :class="t.k === 'report' && report?.blocking_count ? 'text-bad' : 'text-ink-3'">{{ t.n }}</span>
        </button>
      </div>

      <div class="flex-1 min-h-0 overflow-auto mt-3">
        <!-- 正文差异 -->
        <template v-if="tab === 'diff'">
          <div class="flex items-center gap-2 mb-2">
            <div class="seg">
              <button v-for="v in [{ k: 'changes', l: '只看改动' }, { k: 'all', l: '全部' }, { k: 'full', l: '候选稿全文' }]" :key="v.k"
                      class="seg-item" :class="{ 'is-active': diffView === v.k }" @click="diffView = v.k">{{ v.l }}</button>
            </div>
            <span class="hint">红色删除线为当前正文，绿色为候选稿</span>
          </div>
          <div v-if="diffView === 'full'" class="rounded-lg overflow-hidden border border-line doc-desk">
            <MarkdownPreview :content="detail.content" mode="preview" dense />
          </div>
          <p v-else-if="diff && !diff.changed" class="text-xs text-ink-3 py-6 text-center">候选稿与当前正文相同</p>
          <div v-else class="diff-view rounded-lg border border-line bg-surface text-[13px] leading-[1.85]">
            <template v-for="(r, i) in diffRows" :key="i">
              <button v-if="r.type === 'gap'" class="diff-gap" @click="diffView = 'all'">… 未改动 {{ r.count }} 行（点击展开）…</button>
              <div v-else class="diff-row" :class="`is-${r.type}`">
                <span class="diff-sign">{{ { add: '+', del: '−', mod: '~', same: '' }[r.type] }}</span>
                <span v-if="r.type === 'mod'" class="diff-text"><span v-for="(p, j) in r.parts" :key="j" :class="`part-${p.type}`">{{ p.text }}</span></span>
                <span v-else class="diff-text">{{ r.text || ' ' }}</span>
              </div>
            </template>
          </div>
        </template>

        <!-- 检查报告 -->
        <template v-else-if="tab === 'report'">
          <CheckReport v-if="report" :report="report" :show-verify="false" />
          <p v-else class="text-xs text-ink-3 py-6 text-center">该候选稿尚未完成检查，不能采纳</p>
        </template>

        <!-- 所用资料 -->
        <div v-else-if="tab === 'assets'" class="space-y-4 text-xs">
          <section>
            <p class="font-semibold text-ink mb-1.5">企业资料</p>
            <p v-if="!assets.length" class="text-ink-3">没有用到企业资料</p>
            <ul class="space-y-1.5">
              <li v-for="a in assets" :key="`${a.kind}:${a.asset_id}`" class="flex items-center gap-1.5">
                <span class="chip chip-mute shrink-0">{{ ASSET_KIND[a.kind] || a.kind }}</span>
                <span class="truncate flex-1 min-w-0 text-ink-2" :title="a.name">{{ a.name }}</span>
                <span v-if="a.source === 'linked'" class="text-2xs text-ink-3 shrink-0">评分项关联</span>
                <span class="chip shrink-0" :class="ASSET_STATUS[a.status]?.chip || 'chip-mute'">{{ ASSET_STATUS[a.status]?.label || a.status }}</span>
              </li>
            </ul>
            <template v-if="excludedAssets.length">
              <p class="font-medium text-ink-2 mt-3 mb-1.5">已排除，未写入正文</p>
              <ul class="space-y-1.5">
                <li v-for="a in excludedAssets" :key="`x:${a.kind}:${a.asset_id}`" class="leading-relaxed">
                  <span class="chip chip-mute mr-1">{{ ASSET_KIND[a.kind] || a.kind }}</span>
                  <span class="text-ink-2 line-through decoration-ink-3/60">{{ a.name }}</span>
                  <span class="block text-2xs text-bad mt-0.5">{{ a.reason }}</span>
                </li>
              </ul>
            </template>
          </section>
          <section>
            <p class="font-semibold text-ink mb-1.5">知识库参考<span class="num text-ink-3 font-normal ml-1">{{ kbRefs.length }}</span></p>
            <p v-if="!kbRefs.length" class="text-ink-3">没有命中高置信的知识库参考</p>
            <ul class="space-y-1">
              <li v-for="r in kbRefs" :key="r.chunk_id" class="text-ink-2 truncate" :title="`${r.doc_name} / ${r.breadcrumb}`">
                {{ r.section_title || r.breadcrumb }}<span class="text-ink-3"> · {{ r.doc_name }}</span>
              </li>
            </ul>
          </section>
        </div>

        <!-- 待核实事项 -->
        <div v-else class="text-xs">
          <p v-if="!verifyItems.length" class="text-ink-3 py-6 text-center">没有需要核实的承诺数值、占位或资料</p>
          <ul class="space-y-2">
            <li v-for="(v, i) in verifyItems" :key="i" class="leading-relaxed">
              <span class="chip chip-warn mr-1">{{ VERIFY_KIND[v.kind] || v.kind }}</span>
              <span class="text-ink-2">{{ v.text }}</span>
              <span v-if="v.note && v.kind !== 'placeholder'" class="block text-2xs text-ink-3">{{ v.note }}</span>
            </li>
          </ul>
        </div>
      </div>

      <!-- 操作 -->
      <div class="shrink-0 flex flex-wrap items-center gap-2 pt-3 mt-3 border-t border-line">
        <template v-if="isOpen || conflict">
          <el-button v-if="isOpen" :loading="acting === 'reject'" :disabled="!!acting" @click="reject">放弃</el-button>
          <el-tooltip :content="ai.llmConfigured ? '按当前正文的检查问题定向修订，生成新的候选稿' : '定向修订需要先配置大模型'" placement="top">
            <span>
              <el-button :loading="acting === 'revise'" :disabled="!!acting || !ai.llmConfigured || !!reviseTaskId" @click="revise">
                基于当前正文重新修订
              </el-button>
            </span>
          </el-tooltip>
          <span class="flex-1" />
          <el-tooltip v-if="isOpen" :content="staleReason || '写入正文（原正文留存为历史版本）'" placement="top">
            <span>
              <el-button type="primary" :loading="acting === 'apply'" :disabled="!!acting || detail.status !== 'checked' || !!staleReason" @click="apply">
                采纳
              </el-button>
            </span>
          </el-tooltip>
        </template>
        <p v-else class="hint">该候选稿{{ STATUS[detail.status]?.label || detail.status }}{{ detail.decided_at ? `（${detail.decided_at}）` : '' }}。</p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.diff-view {
  padding: 6px 0;
}
.diff-row {
  display: flex;
  gap: 8px;
  padding: 0 12px;
}
.diff-sign {
  width: 12px;
  flex-shrink: 0;
  color: rgb(var(--c-ink-3));
  user-select: none;
}
.diff-text {
  flex: 1;
  min-width: 0;
  white-space: pre-wrap;
  word-break: break-word;
  color: rgb(var(--c-ink));
}
.diff-row.is-same .diff-text {
  color: rgb(var(--c-ink-2));
}
.diff-row.is-add {
  background: rgb(var(--c-ok) / 0.08);
}
.diff-row.is-add .diff-sign {
  color: rgb(var(--c-ok));
}
.diff-row.is-del {
  background: rgb(var(--c-bad) / 0.07);
}
.diff-row.is-del .diff-text {
  text-decoration: line-through;
  text-decoration-color: rgb(var(--c-bad) / 0.6);
  color: rgb(var(--c-ink-2));
}
.diff-row.is-del .diff-sign {
  color: rgb(var(--c-bad));
}
.diff-row.is-mod .diff-sign {
  color: rgb(var(--c-warn));
}
.part-add {
  background: rgb(var(--c-ok) / 0.18);
  border-radius: 2px;
}
.part-del {
  background: rgb(var(--c-bad) / 0.14);
  text-decoration: line-through;
  text-decoration-color: rgb(var(--c-bad) / 0.6);
  border-radius: 2px;
}
.diff-gap {
  display: block;
  width: 100%;
  padding: 2px 12px;
  text-align: left;
  font-size: 12px;
  color: rgb(var(--c-ink-3));
  background: rgb(var(--c-sunken));
}
.diff-gap:hover {
  color: rgb(var(--c-accent-fg));
}
</style>
