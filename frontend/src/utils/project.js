/**
 * 项目阶段的展示元数据（created → tender_analyzed → outline_confirmed → writing）。
 */
export const STAGES = [
  { key: 'created', label: '待上传招标文件', short: '拆标' },
  { key: 'tender_analyzed', label: '待规划大纲', short: '大纲' },
  { key: 'outline_confirmed', label: '大纲已就绪', short: '事实' },
  { key: 'writing', label: '撰写中', short: '撰写' },
]

export function stageIndex(stage) {
  const i = STAGES.findIndex((s) => s.key === stage)
  return i < 0 ? 0 : i
}

export function stageLabel(stage) {
  return STAGES.find((s) => s.key === stage)?.label || stage || ''
}

/** 章节状态：dot 为大纲树里的状态点样式，chip 为胶囊样式 */
export const SECTION_STATUS = {
  pending: { label: '待写', dot: 'border border-ink-3/70', chip: 'chip-mute' },
  generating: { label: '生成中', dot: 'bg-accent animate-pulse', chip: 'chip-accent' },
  completed: { label: '已草拟', dot: 'bg-warn', chip: 'chip-warn' },
  reviewed: { label: '已校审', dot: 'bg-ok', chip: 'chip-ok' },
}

export function sectionStatus(status) {
  return SECTION_STATUS[status] || SECTION_STATUS.pending
}

/** 尚未进入编纂阶段的项目回到向导 */
export function projectEntry(project) {
  return ['created', 'tender_analyzed'].includes(project.stage)
    ? `/project/${project.id}/wizard`
    : `/project/${project.id}/workspace`
}
