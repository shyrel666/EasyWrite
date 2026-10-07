import { describe, expect, it } from 'vitest'
import { isActiveTask, taskRoute, taskStatusMeta, taskTypeLabel, TERMINAL_STATUSES } from '@/utils/tasks'

describe('任务展示元数据', () => {
  it('进行中与终态的划分', () => {
    expect(isActiveTask({ status: 'pending' })).toBe(true)
    expect(isActiveTask({ status: 'running' })).toBe(true)
    for (const status of TERMINAL_STATUSES) expect(isActiveTask({ status })).toBe(false)
    expect(isActiveTask(null)).toBe(false)
    expect(TERMINAL_STATUSES).toContain('interrupted')
  })

  it('已中断任务显示为警告色"已中断"', () => {
    expect(taskStatusMeta({ status: 'interrupted' })).toEqual({ label: '已中断', type: 'warning' })
    expect(taskStatusMeta({ status: 'weird' }).label).toBe('weird')
  })

  it('类型名称，未知类型原样显示', () => {
    expect(taskTypeLabel({ type: 'section_batch' })).toBe('批量撰写')
    expect(taskTypeLabel({ type: 'custom_job' })).toBe('custom_job')
  })

  it('按类型跳转到对应页面', () => {
    expect(taskRoute({ type: 'section_batch', project_id: 'p1' })).toBe('/project/p1/workspace')
    expect(taskRoute({ type: 'tender_analyze', project_id: 'p1' })).toBe('/project/p1/wizard')
    expect(taskRoute({ type: 'deviation_generate', project_id: 'p1' })).toBe('/project/p1/deviation')
    expect(taskRoute({ type: 'compliance_check', project_id: 'p1' })).toBe('/project/p1/quality')
    expect(taskRoute({ type: 'kb_ingest', project_id: '' })).toBe('/knowledge')
    // 没有项目归属的项目类任务（如未关联项目的拆标）无处可跳
    expect(taskRoute({ type: 'tender_analyze', project_id: '' })).toBe('')
  })
})
