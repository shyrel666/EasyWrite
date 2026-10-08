/**
 * 后台任务的展示元数据：类型名称、状态标签、跳转页面。
 */
export const ACTIVE_STATUSES = ['pending', 'running']
export const TERMINAL_STATUSES = ['completed', 'failed', 'cancelled', 'interrupted']

export const TASK_TYPE_LABELS = {
  tender_analyze: '拆标分析',
  kb_ingest: '知识库入库',
  kb_reindex: '向量重建',
  kb_curate: '条目策展',
  compliance_check: '废标红线核查',
  deviation_generate: '偏离表响应',
  section_batch: '批量撰写',
  section_revise: '定向修订',
  section_refine: '智能完善',
}

export const TASK_STATUS_META = {
  pending: { label: '排队中', type: 'info' },
  running: { label: '进行中', type: 'primary' },
  completed: { label: '已完成', type: 'success' },
  failed: { label: '失败', type: 'danger' },
  cancelled: { label: '已取消', type: 'info' },
  interrupted: { label: '已中断', type: 'warning' },
}

export function isActiveTask(task) {
  return !!task && ACTIVE_STATUSES.includes(task.status)
}

export function taskTypeLabel(task) {
  return TASK_TYPE_LABELS[task?.type] || task?.type || '后台任务'
}

// 已结束但未达成目标的任务（如智能完善的 meta.outcome）：不显示为"已完成"
const OUTCOME_STATUS_META = {
  partial: { label: '部分完成', type: 'warning' },
  no_output: { label: '未产出', type: 'warning' },
}

export function taskStatusMeta(task) {
  if (task?.status === 'completed' && OUTCOME_STATUS_META[task.meta?.outcome]) return OUTCOME_STATUS_META[task.meta.outcome]
  return TASK_STATUS_META[task?.status] || { label: task?.status || '未知', type: 'info' }
}

/** 任务对应的查看页面；没有合适页面时返回 ''。章节类任务（meta.section_id）定位到该章节 */
export function taskRoute(task) {
  if (!task) return ''
  if (task.type?.startsWith('kb_')) return '/knowledge'
  const pid = task.project_id
  if (!pid) return ''
  const sectionId = task.meta?.section_id
  if (sectionId) return `/project/${pid}/workspace?section=${encodeURIComponent(sectionId)}`
  return {
    tender_analyze: `/project/${pid}/wizard`,
    section_batch: `/project/${pid}/workspace`,
    deviation_generate: `/project/${pid}/deviation`,
    compliance_check: `/project/${pid}/quality`,
  }[task.type] || `/project/${pid}/workspace`
}
