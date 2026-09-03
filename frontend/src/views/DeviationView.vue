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

const statusMeta = {
  '完全满足': { type: 'success', cls: 'text-emerald-700' },
  '正偏离': { type: 'primary', cls: 'text-blue-700' },
  '负偏离': { type: 'danger', cls: 'text-red-600 font-bold' },
  '待生成': { type: 'warning', cls: 'text-amber-600' },
}

const stats = computed(() => {
  const total = items.value.length
  const star = items.value.filter((i) => i.is_star).length
  const pending = items.value.filter((i) => i.response_status === '待生成').length
  const negative = items.value.filter((i) => i.response_status === '负偏离').length
  return { total, star, pending, negative }
})

async function extract() {
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

async function saveEdits() {
  try {
    await api.saveDeviations(projectId, items.value)
    dirty.value = false
    ElMessage.success('偏离表已保存（人工编辑持久化）')
  } catch (e) {
    ElMessage.error('保存失败：' + e.message)
  }
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
  if (task.result.pending_count > 0) {
    ElMessage.warning(`${task.result.pending_count} 项响应未生成（未配置大模型），请配置后重试`)
  } else {
    ElMessage.success(`点对点响应生成完成：${task.result.total_items} 项`)
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

onMounted(async () => {
  await store.load(projectId)
  const res = await api.getDeviations(projectId)
  items.value = res.items
  outlineLeaves.value = collectLeaves(store.outline)
})
</script>

<template>
  <AppShell :project-id="projectId" :project-name="store.project?.name" active="deviation" export-enabled>
    <div class="max-w-7xl mx-auto px-3 sm:px-6 py-5">
      <!-- 统计卡 -->
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <div class="bg-white rounded-lg border border-slate-200 p-3.5">
          <p class="text-xs text-slate-500">指标总数</p>
          <p class="text-xl font-bold mt-0.5">{{ stats.total }}</p>
        </div>
        <div class="bg-white rounded-lg border border-slate-200 p-3.5">
          <p class="text-xs text-slate-500">★号红线条款</p>
          <p class="text-xl font-bold mt-0.5 text-red-600">{{ stats.star }}</p>
        </div>
        <div class="bg-white rounded-lg border border-slate-200 p-3.5">
          <p class="text-xs text-slate-500">待生成响应</p>
          <p class="text-xl font-bold mt-0.5" :class="stats.pending ? 'text-amber-600' : 'text-emerald-600'">{{ stats.pending }}</p>
        </div>
        <div class="bg-white rounded-lg border border-slate-200 p-3.5">
          <p class="text-xs text-slate-500">负偏离</p>
          <p class="text-xl font-bold mt-0.5" :class="stats.negative ? 'text-red-600' : 'text-emerald-600'">{{ stats.negative }}</p>
        </div>
      </div>

      <!-- 工具栏 -->
      <div class="flex items-center gap-2 mb-4 flex-wrap">
        <el-button type="primary" :loading="extracting" @click="extract">
          <el-icon class="mr-1"><RefreshLeft /></el-icon>从招标文件提取指标
        </el-button>
        <el-button :disabled="!items.length" :loading="generating" @click="generate">
          <el-icon class="mr-1"><MagicStick /></el-icon>AI 批量生成点对点响应
        </el-button>
        <el-button :disabled="!dirty" type="warning" plain @click="saveEdits">保存人工编辑</el-button>
        <div class="flex items-center gap-2 ml-auto text-sm">
          <span class="text-xs text-slate-500">回填至章节：</span>
          <el-select v-model="injectSectionId" size="small" placeholder="选择章节" class="!w-64">
            <el-option v-for="leaf in outlineLeaves" :key="leaf.id" :label="leaf.title" :value="leaf.id" />
          </el-select>
          <el-button size="small" :disabled="!items.length" @click="injectToOutline">回填</el-button>
        </div>
      </div>

      <!-- 任务进度 -->
      <div v-if="generatingTaskId" class="bg-white rounded-lg border border-slate-200 p-4 mb-4">
        <TaskProgress
          :task-id="generatingTaskId"
          title="正在逐项生成点对点技术响应（结合全局事实与企业资产）"
          @done="onGenerateDone"
          @failed="() => { generating = false; ElMessage.error('生成任务失败') }"
        />
      </div>

      <!-- 覆盖率提示 -->
      <el-alert v-if="coverage?.note" type="warning" :closable="false" class="mb-4" :title="coverage.note" />

      <!-- 偏离表 -->
      <EmptyState
        v-if="!items.length"
        icon="List"
        title="偏离表为空"
        description="点击上方按钮从已上传的招标文件正文中确定性提取技术要求清单（★号条款优先置顶）"
      />

      <div v-else class="bg-white rounded-lg border border-slate-200 overflow-hidden">
        <div class="overflow-x-auto">
          <table class="w-full text-sm min-w-[900px]">
            <thead class="bg-slate-50 text-slate-600">
              <tr>
                <th class="px-3 py-2.5 text-left font-medium w-14">序号</th>
                <th class="px-3 py-2.5 text-left font-medium min-w-[260px]">招标文件技术规格要求</th>
                <th class="px-3 py-2.5 text-left font-medium w-24">条款属性</th>
                <th class="px-3 py-2.5 text-left font-medium w-28">响应状态</th>
                <th class="px-3 py-2.5 text-left font-medium min-w-[300px]">点对点技术响应说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(item, i) in items" :key="i" class="border-t border-slate-100 hover:bg-slate-50/60 align-top">
                <td class="px-3 py-2 text-slate-500">{{ item.index }}</td>
                <td class="px-3 py-2">
                  <el-input v-model="item.clause_title" type="textarea" :autosize="{ minRows: 1, maxRows: 3 }" @input="dirty = true" />
                </td>
                <td class="px-3 py-2">
                  <el-tag v-if="item.is_star" type="danger" size="small" effect="dark">★ 红线</el-tag>
                  <el-tag v-else type="info" size="small" effect="plain">一般条款</el-tag>
                </td>
                <td class="px-3 py-2">
                  <el-select v-model="item.response_status" size="small" @change="dirty = true">
                    <el-option v-for="s in ['完全满足', '正偏离', '负偏离', '待生成']" :key="s" :label="s" :value="s" />
                  </el-select>
                </td>
                <td class="px-3 py-2">
                  <el-input v-model="item.response_detail" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }" @input="dirty = true" />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </AppShell>
</template>
