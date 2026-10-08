/**
 * 智能完善（单章节写—查—改闭环）的展示辅助：结果、停止原因、修订方式的名称，以及能否"继续"。
 */
export const MAX_ROUNDS_LIMIT = 3
export const DEFAULT_MAX_ROUNDS = 2

export const OUTCOME_META = {
  goal_met: { label: '已达成检查目标', chip: 'chip-ok', note: 'note-ok' },
  partial: { label: '部分完成', chip: 'chip-warn', note: 'note-warn' },
  no_output: { label: '未产出候选稿', chip: 'chip-bad', note: 'note-bad' },
}

export const STOP_REASON_LABELS = {
  goal_met: '目标达成',
  no_improvement: '修订没有进展',
  rounds_exhausted: '修订轮数用完',
  insufficient_evidence: '资料不足',
  budget_exhausted: '预算用尽',
  cancelled: '已取消',
  error: '运行出错',
}

export const MODE_LABELS = {
  draft: '起草',
  normal: '逐条修订',
  alternate: '换一种修改方式',
  minimal: '只处理阻塞问题',
}

// 这些原因停下时，最后一版候选稿仍可接着修订（轮数不会重新计算）
const RESUMABLE = ['budget_exhausted', 'cancelled', 'error']

export function outcomeMeta(outcome) {
  return OUTCOME_META[outcome] || { label: outcome || '未知', chip: 'chip-mute', note: 'note-info' }
}

export function stopReasonLabel(reason) {
  return STOP_REASON_LABELS[reason] || reason || ''
}

/** 任务结束后能否"继续"：服务重启中断、预算用尽、取消或出错，且已有候选稿 / 轮数未用完 */
export function canResume(task) {
  if (!task) return false
  if (task.status === 'interrupted') return true
  const r = task.result
  if (!r || !r.final_proposal_id) return false
  return RESUMABLE.includes(r.stop_reason) && r.rounds < r.max_rounds
}

/** 候选稿来源：智能完善的起草稿 / 第 n 轮修订稿 */
export function proposalOriginLabel(p) {
  if (!p) return ''
  if (p.origin === 'refine_draft') return '智能完善 · 起草稿'
  if (p.origin === 'refine') {
    const round = p.refine?.round
    const mode = p.refine?.mode && p.refine.mode !== 'normal' ? `（${MODE_LABELS[p.refine.mode] || p.refine.mode}）` : ''
    return `智能完善 · 第 ${round ?? '?'} 轮修订${mode}`
  }
  return { batch: '批量撰写', revise: '定向修订' }[p.origin] || p.origin || 'AI'
}

// 进度步骤：按任务的进度描述定位当前步骤
export const REFINE_STEPS = ['读取依据', '选择资料', '起草 / 检查', '定向修订', '候选稿']

export function refineStep(task) {
  if (!task) return 0
  if (!['pending', 'running'].includes(task.status)) return REFINE_STEPS.length - 1
  const msg = task.message || ''
  if (/轮修订/.test(msg)) return 3
  if (/起草|检查|继续执行/.test(msg)) return 2
  if (/选择资料/.test(msg)) return 1
  return 0
}

/** 运行用量：实际请求数 / 上限，耗时（秒） */
export function budgetText(budget) {
  if (!budget) return ''
  const mins = Math.round((budget.max_seconds || 0) / 60)
  return `模型请求 ${budget.calls}/${budget.max_calls} 次 · 用时 ${Math.round(budget.elapsed_s || 0)} 秒（上限 ${mins} 分钟）`
}
