<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useProjectStore } from '@/stores/project'
import AppShell from '@/components/layout/AppShell.vue'
import TaskProgress from '@/components/common/TaskProgress.vue'
import EmptyState from '@/components/common/EmptyState.vue'

const route = useRoute()
const store = useProjectStore()
const projectId = route.params.id

const qualityReport = ref(null)
const complianceTaskId = ref('')
const complianceReport = ref(null)
const checking = ref(false)
const inspecting = ref(false)

async function runQuality() {
  inspecting.value = true
  try {
    qualityReport.value = await api.qualityInspect(projectId)
    ElMessage.success(`八维质检完成：${qualityReport.value.overall_score} 分`)
  } catch (e) {
    ElMessage.error('质检失败：' + e.message)
  } finally {
    inspecting.value = false
  }
}

async function runCompliance() {
  checking.value = true
  complianceReport.value = null
  try {
    const res = await api.complianceCheck(projectId)
    complianceTaskId.value = res.task_id
  } catch (e) {
    checking.value = false
    ElMessage.error('启动失败：' + e.message)
  }
}

function onComplianceDone(task) {
  checking.value = false
  if (task.status !== 'completed') {
    ElMessage.error('合规审查失败：' + (task.error || '').split('\n')[0])
    return
  }
  complianceReport.value = task.result
  if (task.result.mode === 'rules') {
    ElMessage.warning('当前为关键词快扫模式（精度有限）。配置大模型后自动升级为逐条款证据核查。')
  }
}

const severityMeta = {
  HIGH: { cls: 'bg-red-50 border-red-400 text-red-800', label: '高危' },
  MEDIUM: { cls: 'bg-amber-50 border-amber-400 text-amber-800', label: '中危' },
  LOW: { cls: 'bg-slate-50 border-slate-300 text-slate-700', label: '低危' },
}

onMounted(() => store.load(projectId))
</script>

<template>
  <AppShell :project-id="projectId" :project-name="store.project?.name" active="quality" export-enabled>
    <div class="max-w-6xl mx-auto px-3 sm:px-6 py-5 space-y-4">
      <!-- 合规审查卡 -->
      <div class="bg-white rounded-xl border border-slate-200 p-5">
        <div class="flex items-center justify-between mb-3 flex-wrap gap-2">
          <div>
            <h2 class="font-bold text-slate-800">★号废标红线核查</h2>
            <p class="text-xs text-slate-500 mt-0.5">
              LLM 多轮证据核查：范围界定 → 逐条款比对正文证据 → 输出整改建议。条款清单来自 18 项拆标与偏离表。
            </p>
          </div>
          <el-button type="danger" :loading="checking" @click="runCompliance">
            <el-icon class="mr-1"><Warning /></el-icon>执行废标红线核查
          </el-button>
        </div>

        <div v-if="checking && complianceTaskId" class="mb-3">
          <TaskProgress :task-id="complianceTaskId" title="正在逐条款证据核查标书正文" @done="onComplianceDone" />
        </div>

        <!-- 报告渲染（修复旧版"按钮无渲染"缺陷） -->
        <template v-if="complianceReport">
          <div class="flex items-center gap-3 mb-4">
            <div
              class="px-4 py-2.5 rounded-lg text-sm font-bold"
              :class="complianceReport.passed ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-red-50 text-red-700 border border-red-200'"
            >
              {{ complianceReport.passed ? '✓ 合规自检通过' : '✗ 存在高危废标风险' }}
            </div>
            <div class="text-sm text-slate-600">
              ★条款 {{ complianceReport.total_star_items }} 项 / 已响应 {{ complianceReport.satisfied_star_items }} 项
              <span class="text-xs text-slate-400 ml-1">（{{ complianceReport.mode === 'llm' ? 'LLM 证据核查' : '关键词快扫' }} · 核查 {{ complianceReport.checked_sections }} 个章节）</span>
            </div>
          </div>

          <p class="text-sm text-slate-600 mb-3">{{ complianceReport.summary }}</p>

          <div v-if="complianceReport.risk_items?.length" class="space-y-2">
            <div
              v-for="(r, i) in complianceReport.risk_items"
              :key="i"
              class="border-l-4 rounded-r-lg p-3 text-sm"
              :class="severityMeta[r.severity]?.cls || severityMeta.MEDIUM.cls"
            >
              <p class="font-medium">{{ r.item }}</p>
              <p class="text-xs mt-1 opacity-90">{{ r.reason }}</p>
              <p v-if="r.suggestion" class="text-xs mt-1"><b>整改建议：</b>{{ r.suggestion }}</p>
            </div>
          </div>
          <div v-if="complianceReport.warnings?.length" class="mt-3">
            <p v-for="(w, i) in complianceReport.warnings" :key="i" class="text-xs text-amber-700">⚠ {{ w }}</p>
          </div>
        </template>

        <p v-else-if="!checking" class="text-xs text-slate-400">
          尚未执行合规审查。建议在导出 Word 之前运行一次红线核查。
        </p>
      </div>

      <!-- 八维质检卡 -->
      <div class="bg-white rounded-xl border border-slate-200 p-5">
        <div class="flex items-center justify-between mb-4 flex-wrap gap-2">
          <div>
            <h2 class="font-bold text-slate-800">八维质量全盘体检</h2>
            <p class="text-xs text-slate-500 mt-0.5">完整性 / 响应性 / 专业性 / 合规性 / 一致性 / 图表质量 / 排版规范 / AI痕迹检测</p>
          </div>
          <el-button type="primary" plain :loading="inspecting" @click="runQuality">
            <el-icon class="mr-1"><DataAnalysis /></el-icon>执行八维质检
          </el-button>
        </div>

        <EmptyState v-if="!qualityReport" icon="DataAnalysis" title="尚未执行八维质检" description="质检为规则快扫，秒级完成，可在导出前反复执行" />

        <template v-else>
          <div class="flex items-center gap-4 mb-5 flex-wrap">
            <el-progress
              type="dashboard"
              :percentage="qualityReport.overall_score"
              :width="110"
              :color="qualityReport.overall_score >= 90 ? '#10b981' : qualityReport.overall_score >= 75 ? '#f59e0b' : '#ef4444'"
            />
            <div>
              <p class="font-bold text-slate-800">综合 {{ qualityReport.overall_score }} 分 · {{ qualityReport.rating_level }}</p>
              <p class="text-xs text-slate-500 mt-1 max-w-md">{{ qualityReport.summary }}</p>
              <div class="flex flex-wrap gap-1.5 mt-2">
                <el-tag v-for="p in qualityReport.de_ai_detected_phrases?.slice(0, 6)" :key="p" size="small" type="warning" effect="plain">
                  AI套话：{{ p }}
                </el-tag>
              </div>
            </div>
          </div>

          <div class="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <div
              v-for="d in qualityReport.dimensions"
              :key="d.dimension_name"
              class="border border-slate-200 rounded-lg p-3"
            >
              <div class="flex items-center justify-between mb-1.5">
                <span class="text-sm font-medium text-slate-700">{{ d.dimension_name }}</span>
                <span
                  class="text-xs font-bold"
                  :class="d.score >= 90 ? 'text-emerald-600' : d.score >= 75 ? 'text-amber-600' : 'text-red-600'"
                >{{ d.score }}</span>
              </div>
              <el-progress :percentage="d.score" :show-text="false" :stroke-width="6" :color="d.score >= 90 ? '#10b981' : d.score >= 75 ? '#f59e0b' : '#ef4444'" />
              <p v-if="d.findings?.length" class="text-[11px] text-slate-500 mt-1.5 line-clamp-2">{{ d.findings[0] }}</p>
            </div>
          </div>

          <div v-if="qualityReport.high_risk_defects?.length" class="mt-4">
            <p class="text-sm font-bold text-red-600 mb-1.5">一票否决缺陷：</p>
            <p v-for="(d, i) in qualityReport.high_risk_defects" :key="i" class="text-xs text-red-700">✗ {{ d }}</p>
          </div>
        </template>
      </div>
    </div>
  </AppShell>
</template>
