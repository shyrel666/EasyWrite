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

export function taskStatusMeta(task) {
  return TASK_STATUS_META[task?.status] || { label: task?.status || '未知', type: 'info' }
}

/** 任务对应的查看页面；没有合适页面时返回 '' */
export function taskRoute(task) {
  if (!task) return ''
  if (task.type?.startsWith('kb_')) return '/knowledge'
  const pid = task.project_id
  if (!pid) return ''
  return {
    tender_analyze: `/project/${pid}/wizard`,
    section_batch: `/project/${pid}/workspace`,
    deviation_generate: `/project/${pid}/deviation`,
    compliance_check: `/project/${pid}/quality`,
  }[task.type] || `/project/${pid}/workspace`
}
