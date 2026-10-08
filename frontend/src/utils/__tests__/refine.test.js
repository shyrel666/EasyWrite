import { describe, expect, it } from 'vitest'
import { budgetText, canResume, outcomeMeta, proposalOriginLabel, refineStep, stopReasonLabel } from '@/utils/refine'
import { taskRoute, taskStatusMeta, taskTypeLabel } from '@/utils/tasks'

describe('智能完善展示辅助', () => {
  it('结果与停止原因', () => {
    expect(outcomeMeta('goal_met').label).toBe('已达成检查目标')
    expect(outcomeMeta('partial').chip).toBe('chip-warn')
    expect(outcomeMeta('weird').label).toBe('weird')
    expect(stopReasonLabel('insufficient_evidence')).toBe('资料不足')
    expect(stopReasonLabel('budget_exhausted')).toBe('预算用尽')
  })

  it('预算用尽、取消、出错且轮数未用完时可以继续；服务重启中断也可以', () => {
    const done = (r) => ({ status: 'completed', result: { final_proposal_id: 'p1', rounds: 1, max_rounds: 2, ...r } })
    expect(canResume(done({ stop_reason: 'budget_exhausted' }))).toBe(true)
    expect(canResume(done({ stop_reason: 'error' }))).toBe(true)
    expect(canResume(done({ stop_reason: 'budget_exhausted', rounds: 2 }))).toBe(false)
    expect(canResume(done({ stop_reason: 'no_improvement' }))).toBe(false)
    expect(canResume(done({ stop_reason: 'cancelled', final_proposal_id: null }))).toBe(false)
    expect(canResume({ status: 'interrupted', result: null })).toBe(true)
    expect(canResume(null)).toBe(false)
  })

  it('候选稿来源标明起草稿与修订轮次', () => {
    expect(proposalOriginLabel({ origin: 'refine_draft' })).toBe('智能完善 · 起草稿')
    expect(proposalOriginLabel({ origin: 'refine', refine: { round: 2, mode: 'alternate' } })).toBe('智能完善 · 第 2 轮修订（换一种修改方式）')
    expect(proposalOriginLabel({ origin: 'refine', refine: { round: 1, mode: 'normal' } })).toBe('智能完善 · 第 1 轮修订')
    expect(proposalOriginLabel({ origin: 'batch' })).toBe('批量撰写')
  })

  it('按进度描述定位步骤', () => {
    expect(refineStep({ status: 'running', message: '读取依据' })).toBe(0)
    expect(refineStep({ status: 'running', message: '选择资料' })).toBe(1)
    expect(refineStep({ status: 'running', message: '检查起草稿' })).toBe(2)
    expect(refineStep({ status: 'running', message: '第 1 轮修订（共 2 轮，逐条修订）：处理 3 个问题' })).toBe(3)
    expect(refineStep({ status: 'completed', message: '部分完成' })).toBe(4)
  })

  it('预算用量文案', () => {
    expect(budgetText({ calls: 3, max_calls: 15, elapsed_s: 42.4, max_seconds: 600 })).toBe('模型请求 3/15 次 · 用时 42 秒（上限 10 分钟）')
  })
})

describe('任务列表中的智能完善', () => {
  it('未达成目标的任务不显示为已完成', () => {
    expect(taskStatusMeta({ status: 'completed', meta: { outcome: 'partial' } })).toEqual({ label: '部分完成', type: 'warning' })
    expect(taskStatusMeta({ status: 'completed', meta: { outcome: 'goal_met' } }).label).toBe('已完成')
    expect(taskTypeLabel({ type: 'section_refine' })).toBe('智能完善')
  })

  it('章节类任务跳转到该章节', () => {
    expect(taskRoute({ type: 'section_refine', project_id: 'p1', meta: { section_id: 'sec 1' } }))
      .toBe('/project/p1/workspace?section=sec%201')
  })
})
