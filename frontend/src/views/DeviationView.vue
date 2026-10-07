<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { useProjectStore } from '@/stores/project'
import { useAiStore } from '@/stores/ai'
import AppShell from '@/components/layout/AppShell.vue'
import TaskProgress from '@/components/common/TaskProgress.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import PageHeader from '@/components/common/PageHeader.vue'

const route = useRoute()
const store = useProjectStore()
const ai = useAiStore()
const projectId = route.params.id

const items = ref([])
const loading = ref(false)
const extracting = ref(false)
const generatingTaskId = ref('')
const generating = ref(false)
const coverage = ref(null)
const dirty = ref(false)
const injectSectionId = ref('')
const outlineLeaves = ref([])

const stats = computed(() => {
  const total = items.value.length
  const star = items.value.filter((i) => i.level === 'redline' || i.is_star).length
  const important = items.value.filter((i) => i.level === 'important').length
  const aiDraft = items.value.filter((i) => i.response_source === 'ai').length
  const pending = items.value.filter((i) => i.response_status === '待生成').length
  const negative = items.value.filter((i) => i.response_status === '负偏离').length
  return { total, star, important, pending, negative, aiDraft }
})

async function extract() {
  const answered = items.value.filter((i) => i.response_status !== '待生成').length
  if (answered || dirty.value) {
    try {
      await ElMessageBox.confirm(
        `重新提取将整体替换当前偏离表${answered ? `（含 ${answered} 项已填写的响应）` : ''}${dirty.value ? '，未保存的编辑也会丢弃' : ''}。是否继续？`,
        '覆盖确认',
        { type: 'warning', confirmButtonText: '重新提取', cancelButtonText: '取消' }
      )
    } catch { return }
  }
  extracting.value = true
  try {
    const res = await api.extractDeviations(projectId)
    items.value = res.items
    coverage.value = res.coverage
    dirty.value = false
    ElMessage.success(`已提取 ${res.total_items} 项技术指标（★号 ${stats.value.star} 项）`)
    if (res.coverage?.note) ElMessage.warning(res.coverage.note)
  } catch (e) {
    ElMessage.error('提取失败：' + e.message)
  } finally {
    extracting.value = false
  }
}

const LEVEL_META = {
  redline: { label: '废标红线', chip: 'chip-solid-bad', bar: 'bg-bad' },
  important: { label: '重要条款', chip: 'chip-warn', bar: 'bg-warn' },
  normal: { label: '一般条款', chip: 'chip-mute', bar: 'bg-transparent' },
}
function levelOf(item) {
  return LEVEL_META[item.level] ? item.level : (item.is_star ? 'redline' : 'normal')
}
// ---------- 筛选（保留原始下标，编辑仍作用于同一条目） ----------
const filter = ref('all')
const FILTER_TESTS = {
  all: () => true,
  redline: (it) => levelOf(it) === 'redline',
  important: (it) => levelOf(it) === 'important',
  pending: (it) => it.response_status === '待生成',
  negative: (it) => it.response_status === '负偏离',
  ai: (it) => it.response_source === 'ai',
}
const FILTERS = computed(() => [
  { key: 'all', label: '全部', count: stats.value.total },
  { key: 'redline', label: '废标红线', count: stats.value.star },
  { key: 'important', label: '重要条款', count: stats.value.important },
  { key: 'pending', label: '待生成', count: stats.value.pending },
  { key: 'negative', label: '负偏离', count: stats.value.negative },
  { key: 'ai', label: 'AI 拟稿', count: stats.value.aiDraft },
])
const filteredRows = computed(() =>
  items.value.map((item, i) => ({ item, i })).filter(({ item }) => FILTER_TESTS[filter.value](item))
)
const statCells = computed(() => [
  { label: '指标总数', value: stats.value.total, tone: 'text-ink' },
  { label: '废标红线', value: stats.value.star, tone: stats.value.star ? 'text-bad' : 'text-ink' },
  { label: '重要条款', value: stats.value.important, tone: stats.value.important ? 'text-warn' : 'text-ink' },
  { label: '待生成响应', value: stats.value.pending, tone: stats.value.pending ? 'text-warn' : 'text-ok' },
  { label: '负偏离', value: stats.value.negative, tone: stats.value.negative ? 'text-bad' : 'text-ok' },
  { label: 'AI 拟稿待核实', value: stats.value.aiDraft, tone: stats.value.aiDraft ? 'text-accent-fg' : 'text-ok' },
])

// 人工改动响应后不再是"AI 拟稿"
function markManual(item) {
  item.response_source = 'manual'
  dirty.value = true
}

async function saveEdits() {
  try {
    await api.saveDeviations(projectId, items.value)
    dirty.value = false
    ElMessage.success('偏离表已保存（人工编辑持久化）')
  } catch (e) {
    ElMessage.error('保存失败：' + e.message)
  }
}

// 单独导出偏离表 Word（原生表格）；AI 拟稿与未填写的条款先提醒核实
async function exportTable() {
  if (dirty.value) await saveEdits()
  const { aiDraft, pending, negative } = stats.value
  const warnings = []
  if (aiDraft) warnings.push(`${aiDraft} 条响应为 AI 拟稿、尚未人工核实`)
  if (pending) warnings.push(`${pending} 条尚未填写响应`)
  if (negative) warnings.push(`${negative} 条为负偏离`)
  if (warnings.length) {
    try {
      await ElMessageBox.confirm(`${warnings.join('；')}。偏离表是投标承诺，确认仍要导出？`, '导出前核对', {
        confirmButtonText: '仍要导出',
        cancelButtonText: '返回核对',
        type: 'warning',
      })
    } catch {
      return
    }
  }
  let templateId = 'gov_standard'
  try {
    templateId = localStorage.getItem('easywrite.exportTemplate') || templateId
  } catch { /* 存储不可用时使用默认模板 */ }
  window.open(api.deviationExportUrl(projectId, templateId), '_blank')
}

async function generate() {
  if (dirty.value) await saveEdits()
  try {
    const res = await api.generateDeviations(projectId)
    generatingTaskId.value = res.task_id
    generating.value = true
  } catch (e) {
    ElMessage.error('启动失败：' + e.message)
  }
}

function onGenerateDone(task) {
  generating.value = false
  if (task.status !== 'completed') return
  items.value = task.result.items
  dirty.value = false
  const skipped = task.result.skipped_count ? `，跳过已有响应 ${task.result.skipped_count} 项` : ''
  if (task.result.pending_count > 0) {
    ElMessage.warning(`${task.result.pending_count} 项响应未生成（未配置大模型或生成失败）${skipped}`)
  } else {
    ElMessage.success(`点对点响应生成完成：${task.result.generated_count} 项${skipped}`)
  }
  ai.refresh()
}

async function injectToOutline() {
  if (!injectSectionId.value) {
    ElMessage.warning('请选择要回填的目标章节')
    return
  }
  try {
    await ElMessageBox.confirm('将偏离表 Markdown 表格覆盖写入目标章节正文，是否继续？', '回填确认', { type: 'warning' })
  } catch { return }
  try {
    await api.injectDeviations(projectId, injectSectionId.value)
    ElMessage.success('已回填至目标章节')
  } catch (e) {
    ElMessage.error('回填失败：' + e.message)
  }
}

function collectLeaves(nodes, arr = []) {
  for (const n of nodes || []) {
    if (!n.children || !n.children.length) arr.push(n)
    else collectLeaves(n.children, arr)
  }
  return arr
}

function onGenerateFailed(task) {
  generating.value = false
  ElMessage.error('生成任务失败：' + (task.error || '').split('\n')[0])
}

onMounted(async () => {
  await store.load(projectId)
  const res = await api.getDeviations(projectId)
  items.value = res.items
  outlineLeaves.value = collectLeaves(store.outline)
  // 接管刷新前仍在进行的批量响应生成
  try {
    const { tasks } = await api.listTasks({ projectId, type: 'deviation_generate', active: true, limit: 1 })
    if (tasks.length) {
      generatingTaskId.value = tasks[0].id
      generating.value = true
    }
  } catch { /* 忽略 */ }
})
</script>

<template>
  <AppShell :project-id="projectId" :project-name="store.project?.name" active="deviation" export-enabled>
    <div class="max-w-7xl mx-auto px-4 sm:px-8 py-6 sm:py-10">
      <PageHeader
        eyebrow="技术偏离表"
        title="逐条响应技术要求"
        description="从招标文件的技术 / 服务需求篇确定性提取条款，废标红线与重要条款置顶。响应声明是投标承诺，AI 拟稿须逐条核实。"
      >
        <template #actions>
          <el-button :type="items.length ? 'default' : 'primary'" :loading="extracting" @click="extract">
            <el-icon class="mr-1.5"><RefreshLeft /></el-icon>{{ items.length ? '重新提取' : '从招标文件提取' }}
          </el-button>
          <el-button :type="items.length ? 'primary' : 'default'" :disabled="!items.length" :loading="generating" @click="generate">
            <el-icon class="mr-1.5"><MagicStick /></el-icon>AI 补全待生成
          </el-button>
        </template>
      </PageHeader>

      <!-- 指标条 -->
      <section class="card grid grid-cols-3 lg:grid-cols-6 overflow-hidden mb-5 rise">
        <div v-for="(s, i) in statCells" :key="s.label" class="px-4 py-3.5 border-line" :class="[i < statCells.length - 1 ? 'border-r' : '', i < 3 ? 'border-b lg:border-b-0' : '', i === 2 ? 'border-r-0 lg:border-r' : '']">
          <p class="text-2xs text-ink-3">{{ s.label }}</p>
          <p class="mt-1.5 text-xl font-semibold num" :class="s.tone">{{ s.value }}</p>
        </div>
      </section>

      <!-- 任务进度 -->
      <div v-if="generatingTaskId" class="card p-5 mb-5">
        <TaskProgress
          :task-id="generatingTaskId"
          title="正在逐项生成点对点技术响应（结合全局事实与企业资产）"
          @done="onGenerateDone"
          @failed="onGenerateFailed"
        />
      </div>

      <div class="space-y-2 mb-5" v-if="coverage?.scope?.length || coverage?.note || stats.aiDraft">
        <p v-if="coverage?.scope?.length" class="hint">
          抽取范围：{{ coverage.scope.join('、') }}（仅技术 / 服务需求篇，跳过商务、须知、评标与项目概况）
        </p>
        <div v-if="coverage?.note" class="note note-warn">
          <el-icon class="text-warn mt-0.5 shrink-0"><WarningFilled /></el-icon><span>{{ coverage.note }}</span>
        </div>
        <div v-if="stats.aiDraft" class="note note-info">
          <el-icon class="text-accent-fg mt-0.5 shrink-0"><InfoFilled /></el-icon>
          <span>{{ stats.aiDraft }} 条响应为 AI 拟稿：响应声明是投标承诺，请逐条核实后再导出（修改后自动转为人工确认）。</span>
        </div>
      </div>

      <EmptyState
        v-if="!items.length"
        icon="List"
        title="偏离表为空"
        description="点击「从招标文件提取」，从已上传招标文件的技术 / 服务需求篇提取条款清单。"
      />

      <template v-else>
        <!-- 筛选与操作 -->
        <div class="flex items-center gap-3 mb-3 flex-wrap">
          <div class="seg overflow-x-auto max-w-full">
            <button
              v-for="f in FILTERS"
              :key="f.key"
              class="seg-item"
              :class="{ 'is-active': filter === f.key }"
              @click="filter = f.key"
            >
              {{ f.label }}<span class="ml-1 num text-ink-3">{{ f.count }}</span>
            </button>
          </div>
          <div class="flex items-center gap-2 ml-auto flex-wrap">
            <el-button v-if="dirty" type="warning" plain @click="saveEdits">
              <el-icon class="mr-1.5"><Finished /></el-icon>保存人工编辑
            </el-button>
            <el-button @click="exportTable">
              <el-icon class="mr-1.5"><Download /></el-icon>导出 Word
            </el-button>
            <el-popover trigger="click" placement="bottom-end" :width="320">
              <template #reference>
                <el-button><el-icon class="mr-1.5"><Bottom /></el-icon>回填到章节</el-button>
              </template>
              <p class="text-sm font-semibold text-ink mb-1">回填到标书章节</p>
              <p class="hint mb-3">以 Markdown 表格覆盖写入所选章节正文（覆盖前自动留存历史版本）。</p>
              <el-select v-model="injectSectionId" filterable placeholder="选择章节" class="w-full mb-3">
                <el-option v-for="leaf in outlineLeaves" :key="leaf.id" :label="leaf.title" :value="leaf.id" />
              </el-select>
              <el-button type="primary" class="w-full" :disabled="!injectSectionId" @click="injectToOutline">回填</el-button>
            </el-popover>
          </div>
        </div>

        <div class="card overflow-hidden">
          <div class="overflow-x-auto">
            <table class="table-clean text-[13px] min-w-[960px]">
              <thead>
                <tr>
                  <th class="w-14 !pl-5">序号</th>
                  <th class="min-w-[280px]">招标文件技术规格要求</th>
                  <th class="w-24">条款属性</th>
                  <th class="w-32">响应状态</th>
                  <th class="min-w-[320px]">点对点技术响应说明</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="{ item, i } in filteredRows" :key="i" class="hover:bg-raised/60 transition-colors">
                  <td class="!pl-5 relative">
                    <span class="absolute left-0 top-2 bottom-2 w-[3px] rounded-r" :class="LEVEL_META[levelOf(item)].bar" />
                    <span class="text-ink-3 num leading-8">{{ item.index }}</span>
                  </td>
                  <td>
                    <el-input v-model="item.clause_title" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }" @input="dirty = true" />
                    <p v-if="item.section" class="text-2xs text-ink-3 mt-1 truncate" :title="item.section">{{ item.section }}</p>
                  </td>
                  <td class="pt-3">
                    <span class="chip" :class="LEVEL_META[levelOf(item)].chip">{{ LEVEL_META[levelOf(item)].label }}</span>
                  </td>
                  <td>
                    <el-select v-model="item.response_status" size="default" @change="markManual(item)">
                      <el-option v-for="s in ['完全满足', '正偏离', '负偏离', '待生成']" :key="s" :label="s" :value="s" />
                    </el-select>
                    <span v-if="item.response_source === 'ai'" class="chip chip-accent mt-1.5">AI 拟稿 · 待核实</span>
                  </td>
                  <td>
                    <el-input
                      v-model="item.response_detail"
                      type="textarea"
                      :autosize="{ minRows: 1, maxRows: 5 }"
                      :class="{ 'deviation-negative': item.response_status === '负偏离' }"
                      @input="markManual(item)"
                    />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-if="!filteredRows.length" class="py-10 text-center text-xs text-ink-3">当前筛选下没有条款</p>
        </div>
      </template>
    </div>
  </AppShell>
</template>

<style scoped>
.deviation-negative :deep(.el-textarea__inner) {
  box-shadow: 0 0 0 1px rgb(var(--c-bad) / 0.45) inset;
}
</style>
