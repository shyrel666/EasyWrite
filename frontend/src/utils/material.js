/**
 * 证明材料检查结果的展示（与后端 services/assets/material_check.py 的状态一致）。
 * 状态按严重程度排列：示例资料 > 过期 > 主体不符 > 缺附件 > 待核实 > 齐备；评分项未关联资料时为"未关联"。
 */
export const MATERIAL_KINDS = ['qualifications', 'personnel', 'cases']

export const MATERIAL_CHIP = {
  齐备: 'chip-ok',
  缺附件: 'chip-warn',
  待核实: 'chip-warn',
  过期: 'chip-bad',
  主体不符: 'chip-bad',
  示例资料: 'chip-mute',
  未关联: 'chip-bad',
}

export const ATTACHMENT_ACCEPT = '.pdf,.png,.jpg,.jpeg,.gif,.bmp,.webp,.tif,.tiff'

export function materialChip(status) {
  return MATERIAL_CHIP[status] || 'chip-mute'
}

export function formatSize(bytes) {
  if (!bytes && bytes !== 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** 资料显示名：业绩用项目名称，其余用名称 */
export function assetName(kind, item) {
  return (kind === 'cases' ? item?.project_name : item?.name) || item?.id || ''
}
