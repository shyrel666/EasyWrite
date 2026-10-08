<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import ProposalPanel from '@/components/workspace/ProposalPanel.vue'
import {
  MODE_LABELS, REFINE_STEPS, budgetText, canResume, outcomeMeta, refineStep, stopReasonLabel,
} from '@/utils/refine'
import { formatTokens } from '@/utils/llmUsage'

/**
 * 智能完善面板：运行中显示当前步骤与进度（任务对象由章节编辑器轮询后传入），
 * 结束后显示结果与原因（未达成目标时明确说明）、各版检查摘要、资料缺口，并打开最后一版候选稿。
 */
const props = defineProps({
  projectId: { type: String, required: true },
  sectionId: { type: String, required: true },
  task: { type: Object, default: null },
  resuming: { type: Boolean, default: false },
})
// resume：继续执行；restart：重新发起；applied / changed：透传候选稿面板的采纳与状态变化
const emit = defineEmits(['resume', 'restart', 'applied', 'changed'])

const active = computed(() => ['pending', 'running'].includes(props.task?.status))
const result = computed(() => props.task?.result || null)
const step = computed(() => refineStep(props.task))
const meta = computed(() => outcomeMeta(result.value?.outcome))
const resumable = computed(() => canResume(props.task))
const blockingLeft = computed(() => (result.value?.unresolved || []).filter((i) => i.level === 'blocking'))

const cancelling = ref(false)
async function cancel() {
  cancelling.value = true
  try {
    await api.cancelTask(props.task.id)
    ElMessage.info('正在停止：当前这一步完成后结束，已产生的候选稿保留')
  } catch { /* 任务可能已结束 */ }
}
</script>

<template>
  <div class="text-sm space-y-4">
    <!-- 进行中 -->
    <section v-if="active" class="card px-4 py-4">
      <ol class="flex items-center gap-1.5 flex-wrap text-2xs">
        <li v-for="(s, i) in REFINE_STEPS" :key="s" class="flex items-center gap-1.5">
          <span
            class="inline-flex items-center gap-1 rounded-full px-2 py-0.5"
            :class="i < step ? 'bg-ok/10 text-ok' : i === step ? 'bg-accent-soft text-accent-fg font-medium' : 'bg-sunken text-ink-3'"
          >
            <el-icon v-if="i < step" :size="11"><Check /></el-icon>{{ s }}
          </span>
          <span v-if="i < REFINE_STEPS.length - 1" class="text-ink-3">›</span>
        </li>
      </ol>
      <div class="meter !h-2 mt-4"><span class="bg-accent" :style="{ width: `${task.progress || 0}%` }" /></div>
      <div class="flex items-center gap-3 mt-2">
        <p class="flex-1 text-xs leading-relaxed" :class="task.reconnecting ? 'text-warn' : 'text-ink-2'">
          {{ task.reconnecting ? '与服务的连接中断，正在等待服务恢复…' : task.message || '已提交' }}
        </p>
        <el-button v-if="task.status === 'running'" size="small" text type="danger" :loading="cancelling" @click="cancel">
          {{ cancelling ? '正在停止…' : '停止' }}
        </el-button>
      </div>
      <p class="hint mt-3">每一版都存为带检查报告的候选稿，不直接改动正文；运行期间修改本节正文，候选稿将无法直接采纳。</p>
    </section>

    <!-- 失败 / 中断 -->
    <p v-else-if="task?.status === 'failed'" class="note note-bad">
      <el-icon class="mt-0.5 text-bad shrink-0"><CircleCloseFilled /></el-icon>
      <span class="flex-1"><b>智能完善失败：</b>{{ (task.error || task.message || '未知错误').split('\n')[0] }}</span>
    </p>
    <p v-else-if="task?.status === 'interrupted'" class="note note-warn">
      <el-icon class="mt-0.5 text-warn shrink-0"><Warning /></el-icon>
      <span class="flex-1"><b>已中断：</b>服务在任务完成前重启。可从最后一版候选稿继续，已用的修订轮数不会重新计算。</span>
    </p>

    <!-- 结果 -->
    <template v-if="result && !active">
      <div class="note" :class="meta.note">
        <span class="flex-1 leading-relaxed">
          <span class="chip mr-1.5" :class="meta.chip">{{ meta.label }}</span>
          <b v-if="result.stop_reason && result.stop_reason !== 'goal_met'">{{ stopReasonLabel(result.stop_reason) }}：</b>{{ result.message }}
        </span>
      </div>
      <p v-if="result.applied" class="note note-ok">
        <el-icon class="mt-0.5 text-ok shrink-0"><CircleCheckFilled /></el-icon>已按采纳流程写入正文（本节原为空白）。
      </p>
      <p v-else-if="result.apply_error" class="note note-warn">
        <el-icon class="mt-0.5 text-warn shrink-0"><Warning /></el-icon>{{ result.apply_error }}
      </p>

      <section class="card px-4 py-3.5 space-y-3">
        <div class="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs text-ink-2">
          <span>修订 <b class="num text-ink">{{ result.rounds }}</b> / {{ result.max_rounds }} 轮</span>
          <span class="num">{{ budgetText(result.budget) }}</span>
          <span v-if="result.usage" class="num">
            Token {{ result.usage.calls_without_usage ? `${formatTokens(result.usage.total_tokens)}（${result.usage.calls_without_usage} 次未返回用量）` : formatTokens(result.usage.total_tokens) }}
          </span>
        </div>
        <table v-if="result.history?.length" class="table-clean w-full text-xs">
          <thead>
            <tr><th class="text-left">版本</th><th class="text-left">修订方式</th><th class="!text-right">阻塞问题</th><th class="!text-right">质量问题</th></tr>
          </thead>
          <tbody>
            <tr v-for="(h, i) in result.history" :key="i" :class="{ 'font-medium': h.proposal_id && h.proposal_id === result.final_proposal_id }">
              <td>{{ h.label }}<span v-if="h.proposal_id && h.proposal_id === result.final_proposal_id" class="chip chip-accent ml-1.5">最后一版</span></td>
              <td class="text-ink-3">{{ MODE_LABELS[h.mode] || '—' }}</td>
              <td class="text-right num" :class="h.blocking_count ? 'text-bad' : 'text-ok'">{{ h.blocking_count }}</td>
              <td class="text-right num text-ink-2">{{ h.quality_count }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="blockingLeft.length" class="text-xs">
          <p class="field-label">未解决的阻塞问题</p>
          <ul class="space-y-1 text-ink-2 leading-relaxed">
            <li v-for="(i, k) in blockingLeft" :key="k" class="flex gap-1.5"><span class="dot bg-bad mt-1.5 shrink-0" />{{ i.message }}</li>
          </ul>
        </div>
        <div v-if="result.evidence_gaps?.length" class="text-xs">
          <p class="field-label">资料缺口</p>
          <ul class="space-y-1 text-ink-2 leading-relaxed">
            <li v-for="g in result.evidence_gaps" :key="g" class="flex gap-1.5"><span class="dot bg-warn mt-1.5 shrink-0" />{{ g }}</li>
          </ul>
          <p class="hint mt-1">缺少依据的内容以【待填写】【待核实】占位，不会补写；补充知识库或企业资料后可重新发起。</p>
        </div>
      </section>
    </template>

    <div v-if="!active && task" class="flex flex-wrap items-center gap-2">
      <el-button v-if="resumable" type="primary" plain :loading="resuming" @click="emit('resume')">
        <el-icon class="mr-1"><VideoPlay /></el-icon>继续（从最后一版接着修订）
      </el-button>
      <el-button :disabled="resuming" @click="emit('restart')">重新发起</el-button>
    </div>

    <!-- 最后一版候选稿：差异、检查报告、所用资料、待核实；采纳 / 放弃 -->
    <section v-if="!active && result?.final_proposal_id" class="pt-4 border-t border-line">
      <p class="text-[13px] font-semibold text-ink mb-3">最后一版候选稿</p>
      <ProposalPanel
        :key="result.final_proposal_id"
        :project-id="projectId"
        :section-id="sectionId"
        :proposal-id="result.final_proposal_id"
        @applied="(e) => emit('applied', e)"
        @changed="emit('changed')"
      />
    </section>
  </div>
</template>
