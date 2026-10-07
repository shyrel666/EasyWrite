<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useProjectStore } from '@/stores/project'
import AppShell from '@/components/layout/AppShell.vue'
import TaskProgress from '@/components/common/TaskProgress.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import EvidenceLinkDialog from '@/components/evidence/EvidenceLinkDialog.vue'
import { isActiveTask } from '@/utils/tasks'
import { materialChip } from '@/utils/material'

const route = useRoute()
const store = useProjectStore()
const projectId = route.params.id

const qualityReport = ref(null)
const complianceTaskId = ref('')
const complianceReport = ref(null)
const complianceCheckedAt = ref('')
const complianceInterrupted = ref(null)
const checking = ref(false)
const inspecting = ref(false)

async function runQuality({ quiet = false } = {}) {
  inspecting.value = true
  try {
    const [report, evidence] = await Promise.all([api.qualityInspect(projectId), api.evidence(projectId)])
    qualityReport.value = report
    evidenceItems.value = Object.fromEntries(evidence.items.map((it) => [it.item_id, it]))
    if (!quiet) ElMessage.success(`八维质检完成：${report.overall_score} 分`)
  } catch (e) {
    ElMessage.error('质检失败：' + e.message)
  } finally {
    inspecting.value = false
  }
}

// ---- 证明材料：评分项关联企业资料（建议须确认），与文字覆盖分开统计 ----
const evidenceItems = ref({})
const linkOpen = ref(false)
const linkItem = ref(null)

function openLink(itemId) {
  linkItem.value = evidenceItems.value[itemId] || null
  if (linkItem.value) linkOpen.value = true
}

async function runCompliance() {
  checking.value = true
  complianceReport.value = null
  complianceInterrupted.value = null
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
    if (task.status === 'interrupted') complianceInterrupted.value = task
    ElMessage.error('合规审查失败：' + (task.error || '').split('\n')[0])
    return
  }
  complianceReport.value = task.result
  complianceCheckedAt.value = task.updated_at || ''
  if (task.result.mode === 'rules') {
    ElMessage.warning('当前为关键词快扫模式（精度有限）。配置大模型后自动升级为逐条款证据核查。')
  }
}

const severityMeta = {
  HIGH: { chip: 'chip-solid-bad', label: '高危' },
  MEDIUM: { chip: 'chip-warn', label: '中危' },
  LOW: { chip: 'chip-mute', label: '低危' },
}

onMounted(async () => {
  store.load(projectId)
  // 合规审查结果随任务落库：刷新页面后显示上次核查结果，或接管仍在进行的核查
  try {
    const last = await api.latestTask(projectId, 'compliance_check')
    if (isActiveTask(last)) {
      checking.value = true
      complianceTaskId.value = last.id
    } else if (last?.status === 'completed' && last.result) {
      complianceReport.value = last.result
      complianceCheckedAt.value = last.updated_at
    } else if (last?.status === 'interrupted') {
      complianceInterrupted.value = last
    }
  } catch { /* 忽略 */ }
})

const COVERAGE_CHIP = { '已覆盖': 'chip-ok', '要点缺失': 'chip-warn', '篇幅不足': 'chip-warn', '未撰写': 'chip-bad', '未承接': 'chip-bad' }
function scoreColor(score) {
  if (score == null) return 'rgb(var(--c-line-strong))'
  return score >= 90 ? 'rgb(var(--c-ok))' : score >= 75 ? 'rgb(var(--c-warn))' : 'rgb(var(--c-bad))'
}
</script>

<template>
  <AppShell :project-id="projectId" :project-name="store.project?.name" active="quality" export-enabled>
    <div class="max-w-6xl mx-auto px-4 sm:px-8 py-6 sm:py-10 space-y-6">
      <PageHeader
        eyebrow="质检与合规"
        title="导出前的最后一道关"
        description="先核查废标红线，再做八维质量体检。核查与质检只读不改，修改正文后请重新执行。"
      />

      <!-- ===== 红线核查 ===== -->
      <section class="card overflow-hidden rise">
        <div class="p-5 sm:p-6 flex items-start justify-between gap-4 flex-wrap">
          <div class="flex gap-4 min-w-0">
            <span class="w-10 h-10 rounded-xl bg-bad/10 text-bad flex items-center justify-center shrink-0">
              <el-icon :size="20"><Warning /></el-icon>
            </span>
            <div class="min-w-0">
              <h2 class="text-base font-semibold text-ink">★ 号废标红线核查</h2>
              <p class="text-xs text-ink-2 mt-1 leading-relaxed max-w-xl">
                LLM 多轮证据核查：界定范围 → 逐条款比对正文证据 → 输出整改建议。条款清单来自拆标结果与偏离表。
              </p>
            </div>
          </div>
          <el-button type="danger" :loading="checking" @click="runCompliance">
            {{ complianceReport ? '重新核查' : '执行红线核查' }}
          </el-button>
        </div>

        <div v-if="checking && complianceTaskId" class="px-5 sm:px-6 pb-5">
          <TaskProgress
            :task-id="complianceTaskId"
            title="正在逐条款证据核查标书正文"
            @done="onComplianceDone"
            @failed="onComplianceDone"
          />
        </div>

        <div v-if="complianceInterrupted && !checking" class="px-5 sm:px-6 pb-5">
          <div class="note note-warn">
            <el-icon class="text-warn mt-0.5 shrink-0"><WarningFilled /></el-icon>
            <span>上次核查（{{ complianceInterrupted.created_at }}）因服务重启中断，请重新执行。</span>
          </div>
        </div>

        <template v-if="complianceReport">
          <div
            class="mx-5 sm:mx-6 mb-5 rounded-xl border px-5 py-4 flex items-center gap-5 flex-wrap"
            :class="complianceReport.passed ? 'border-ok/30 bg-ok/[0.06]' : 'border-bad/30 bg-bad/[0.05]'"
          >
            <span
              class="w-11 h-11 rounded-full flex items-center justify-center text-white shrink-0"
              :class="complianceReport.passed ? 'bg-ok' : 'bg-bad'"
            >
              <el-icon :size="22"><component :is="complianceReport.passed ? 'Select' : 'CloseBold'" /></el-icon>
            </span>
            <div class="flex-1 min-w-[12rem]">
              <p class="text-[15px] font-semibold" :class="complianceReport.passed ? 'text-ok' : 'text-bad'">
                {{ complianceReport.passed ? '合规自检通过' : '存在高危废标风险' }}
              </p>
              <p class="text-xs text-ink-2 mt-1">
                {{ complianceReport.mode === 'llm' ? 'LLM 证据核查' : '关键词快扫' }} · 核查 {{ complianceReport.checked_sections }} 个章节
                <template v-if="complianceCheckedAt"> · {{ complianceCheckedAt }}（此后修改过正文请重新核查）</template>
              </p>
            </div>
            <div class="text-right">
              <p class="text-2xl font-semibold num leading-none text-ink">
                {{ complianceReport.satisfied_star_items }}<span class="text-sm text-ink-3 font-normal"> / {{ complianceReport.total_star_items }}</span>
              </p>
              <p class="text-2xs text-ink-3 mt-1.5">★ 条款已响应</p>
            </div>
          </div>

          <div class="px-5 sm:px-6 pb-6 space-y-3">
            <p v-if="complianceReport.summary" class="text-sm text-ink-2 leading-relaxed">{{ complianceReport.summary }}</p>
            <div v-if="complianceReport.risk_items?.length" class="space-y-2">
              <article
                v-for="(r, i) in complianceReport.risk_items"
                :key="i"
                class="rounded-lg border border-line p-3.5 flex gap-3"
              >
                <span class="chip shrink-0 h-fit" :class="(severityMeta[r.severity] || severityMeta.MEDIUM).chip">
                  {{ (severityMeta[r.severity] || severityMeta.MEDIUM).label }}
                </span>
                <div class="min-w-0 text-sm">
                  <p class="font-medium text-ink leading-snug">{{ r.item }}</p>
                  <p class="text-xs text-ink-2 mt-1 leading-relaxed">{{ r.reason }}</p>
                  <p v-if="r.suggestion" class="text-xs mt-2 leading-relaxed text-ink">
                    <span class="font-semibold text-accent-fg">整改建议 </span>{{ r.suggestion }}
                  </p>
                </div>
              </article>
            </div>
            <div v-if="complianceReport.warnings?.length" class="space-y-1">
              <p v-for="(w, i) in complianceReport.warnings" :key="i" class="text-xs text-warn flex gap-1.5">
                <el-icon class="mt-0.5 shrink-0"><WarningFilled /></el-icon>{{ w }}
              </p>
            </div>
          </div>
        </template>

        <p v-else-if="!checking" class="px-5 sm:px-6 pb-6 -mt-1 hint">尚未执行红线核查。建议在导出 Word 之前运行一次。</p>
      </section>

      <!-- ===== 八维质检 ===== -->
      <section class="card overflow-hidden rise" style="animation-delay: 60ms">
        <div class="p-5 sm:p-6 flex items-start justify-between gap-4 flex-wrap">
          <div class="flex gap-4 min-w-0">
            <span class="w-10 h-10 rounded-xl bg-accent-soft text-accent-fg flex items-center justify-center shrink-0">
              <el-icon :size="20"><DataAnalysis /></el-icon>
            </span>
            <div class="min-w-0">
              <h2 class="text-base font-semibold text-ink">八维质量体检</h2>
              <p class="text-xs text-ink-2 mt-1">完整性 · 响应性 · 专业性 · 合规性 · 一致性 · 图表质量 · 排版规范 · AI 痕迹</p>
            </div>
          </div>
          <el-button type="primary" :plain="!!qualityReport" :loading="inspecting" @click="runQuality">
            {{ qualityReport ? '重新质检' : '执行八维质检' }}
          </el-button>
        </div>

        <EmptyState v-if="!qualityReport" compact icon="DataAnalysis" title="尚未执行八维质检" description="规则快扫，秒级完成，可在导出前反复执行。" />

        <template v-else>
          <div class="px-5 sm:px-6 pb-6 flex items-center gap-6 flex-wrap">
            <div class="relative w-[120px] h-[120px] shrink-0">
              <svg viewBox="0 0 120 120" class="w-full h-full -rotate-90">
                <circle cx="60" cy="60" r="52" fill="none" class="stroke-sunken" stroke-width="10" />
                <circle
                  cx="60" cy="60" r="52" fill="none" stroke-width="10" stroke-linecap="round"
                  :style="{ stroke: scoreColor(qualityReport.overall_score) }"
                  :stroke-dasharray="`${(qualityReport.overall_score || 0) * 3.267} 326.7`"
                  class="transition-[stroke-dasharray] duration-700"
                />
              </svg>
              <div class="absolute inset-0 flex flex-col items-center justify-center">
                <span class="text-3xl font-semibold num leading-none text-ink">{{ qualityReport.overall_score ?? '—' }}</span>
                <span class="text-2xs text-ink-3 mt-1">综合得分</span>
              </div>
            </div>
            <div class="flex-1 min-w-[14rem]">
              <p class="text-lg font-semibold text-ink">{{ qualityReport.rating_level }}</p>
              <p class="text-sm text-ink-2 mt-1 leading-relaxed max-w-xl">{{ qualityReport.summary }}</p>
              <div v-if="qualityReport.de_ai_detected_phrases?.length" class="flex flex-wrap gap-1.5 mt-3">
                <span v-for="p in qualityReport.de_ai_detected_phrases.slice(0, 8)" :key="p" class="chip chip-warn">AI 套话：{{ p }}</span>
              </div>
            </div>
          </div>

          <div class="grid sm:grid-cols-2 lg:grid-cols-4 gap-px bg-line border-t border-line">
            <div v-for="d in qualityReport.dimensions" :key="d.dimension_name" class="bg-surface p-4 sm:p-5">
              <div class="flex items-baseline justify-between mb-2.5">
                <span class="text-[13px] font-medium text-ink">{{ d.dimension_name }}</span>
                <span v-if="d.score == null" class="text-2xs text-ink-3">未检测</span>
                <span v-else class="text-base font-semibold num" :class="d.score >= 90 ? 'text-ok' : d.score >= 75 ? 'text-warn' : 'text-bad'">{{ d.score }}</span>
              </div>
              <div class="meter"><span :style="{ width: `${d.score ?? 0}%`, background: scoreColor(d.score) }" /></div>
              <p v-if="d.findings?.length" class="text-2xs text-ink-3 mt-2.5 line-clamp-2 leading-relaxed" :title="d.findings.join('\n')">{{ d.findings[0] }}</p>
            </div>
          </div>

          <!-- 评分点覆盖 -->
          <div v-if="qualityReport.scoring_coverage?.length" class="border-t border-line">
            <div class="px-5 sm:px-6 pt-5 pb-3 flex items-end justify-between gap-3 flex-wrap">
              <div>
                <h3 class="text-sm font-semibold text-ink">评分点覆盖核查</h3>
                <p class="hint mt-0.5">只核查“有没有写到”，写得好不好需人工评审</p>
              </div>
              <div class="flex items-center gap-3">
                <span class="text-xs text-ink-2">按分值加权覆盖率</span>
                <div class="meter w-28"><span class="bg-ok" :style="{ width: `${Math.round(qualityReport.scoring_coverage_rate * 100)}%` }" /></div>
                <span class="text-sm font-semibold num">{{ Math.round(qualityReport.scoring_coverage_rate * 100) }}%</span>
                <template v-if="qualityReport.material_total">
                  <span class="w-px h-4 bg-line mx-1" />
                  <span class="text-xs text-ink-2">证明材料齐备</span>
                  <span class="text-sm font-semibold num">{{ qualityReport.material_complete }}<span class="text-ink-3 font-normal">/{{ qualityReport.material_total }}</span></span>
                </template>
              </div>
            </div>
            <div class="overflow-x-auto">
              <table class="table-clean min-w-[760px]">
                <thead>
                  <tr>
                    <th class="!pl-5 sm:!pl-6">评分项</th>
                    <th class="w-16">分值</th>
                    <th class="w-24">状态</th>
                    <th>承接章节</th>
                    <th class="w-28">字数 / 预算</th>
                    <th>缺失要点</th>
                    <th class="w-32">证明材料</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="c in qualityReport.scoring_coverage" :key="c.item_id">
                    <td class="!pl-5 sm:!pl-6 text-ink font-medium">{{ c.name }}</td>
                    <td class="num">{{ c.points ?? '?' }}</td>
                    <td><span class="chip" :class="COVERAGE_CHIP[c.status] || 'chip-mute'">{{ c.status }}</span></td>
                    <td class="text-ink-2">{{ c.section_titles.join('、') || '—' }}</td>
                    <td class="text-ink-2 num">{{ c.word_count }}<template v-if="c.word_budget"> / {{ c.word_budget }}</template></td>
                    <td class="text-warn">{{ c.missing_points.join('、') }}</td>
                    <td>
                      <button
                        v-if="c.material_status"
                        class="chip hover:ring-1 hover:ring-current/30"
                        :class="materialChip(c.material_status)"
                        :title="[...(c.material_assets.length ? [`已关联：${c.material_assets.join('、')}`] : []), ...c.material_notes].join('\n') || '点击关联证明资料'"
                        @click="openLink(c.item_id)"
                      >{{ c.material_status }}<el-icon class="ml-0.5"><EditPen /></el-icon></button>
                      <span v-else class="text-ink-3">—</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <div v-if="qualityReport.high_risk_defects?.length" class="border-t border-line p-5 sm:p-6">
            <p class="text-sm font-semibold text-bad mb-2">一票否决缺陷</p>
            <ul class="space-y-1">
              <li v-for="(d, i) in qualityReport.high_risk_defects" :key="i" class="text-xs text-ink flex gap-2">
                <el-icon class="text-bad mt-0.5 shrink-0"><CircleCloseFilled /></el-icon>{{ d }}
              </li>
            </ul>
          </div>
        </template>
      </section>
    </div>
    <EvidenceLinkDialog v-model="linkOpen" :project-id="projectId" :item="linkItem" @saved="runQuality({ quiet: true })" />
  </AppShell>
</template>
