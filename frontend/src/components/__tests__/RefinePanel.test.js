import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'
import RefinePanel from '@/components/workspace/RefinePanel.vue'
import api from '@/api/client'

function mountPanel(task) {
  const pinia = createPinia()
  setActivePinia(pinia)
  return mount(RefinePanel, {
    props: { projectId: 'p1', sectionId: 's1', task },
    global: { plugins: [pinia, ElementPlus] },
  })
}

const result = (r) => ({
  section_id: 's1', outcome: 'partial', stop_reason: 'budget_exhausted', message: '预算用尽：模型请求次数已达本次运行上限（15 次）',
  final_proposal_id: 'prop_1', rounds: 1, max_rounds: 2,
  history: [
    { round: 0, label: '起草稿', mode: 'draft', proposal_id: 'prop_0', blocking_count: 2, quality_count: 1 },
    { round: 1, label: '第 1 轮修订', mode: 'normal', proposal_id: 'prop_1', blocking_count: 1, quality_count: 0 },
  ],
  unresolved: [{ level: 'blocking', message: '本节承接的评分要点未写到：季度巡检安排' }],
  evidence_gaps: ['知识库中没有本节的高置信参考'],
  budget: { calls: 15, max_calls: 15, elapsed_s: 80, max_seconds: 600 },
  usage: { total_tokens: 1200, calls_without_usage: 0 },
  applied: false, apply_error: '',
  ...r,
})

describe('RefinePanel', () => {
  beforeEach(() => {
    vi.spyOn(api, 'getProposal').mockResolvedValue({
      id: 'prop_1', status: 'checked', origin: 'refine', refine: { round: 1, mode: 'normal' }, content: '候选稿',
      created_at: '2026-10-08 09:00:00', char_count: 3, report: null, evidence: [], state: { current_content: '' },
    })
  })
  afterEach(() => vi.restoreAllMocks())

  it('运行中显示当前步骤与进度描述', () => {
    const wrapper = mountPanel({ id: 't1', status: 'running', progress: 50, message: '第 1 轮修订（共 2 轮，逐条修订）：处理 2 个问题' })
    expect(wrapper.text()).toContain('定向修订')
    expect(wrapper.text()).toContain('处理 2 个问题')
    expect(wrapper.text()).not.toContain('最后一版候选稿')
  })

  it('未达成目标：显示"部分完成"与原因、未解决的问题、资料缺口，可以继续', async () => {
    const wrapper = mountPanel({ id: 't1', status: 'completed', progress: 100, message: '', result: result() })
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('部分完成')
    expect(text).not.toContain('已达成检查目标')
    expect(text).toContain('预算用尽：')
    expect(text).toContain('季度巡检安排')
    expect(text).toContain('知识库中没有本节的高置信参考')
    expect(text).toContain('模型请求 15/15 次')
    const resume = wrapper.findAll('button').find((b) => b.text().includes('继续'))
    expect(resume).toBeTruthy()
    await resume.trigger('click')
    expect(wrapper.emitted('resume')).toHaveLength(1)
    expect(api.getProposal).toHaveBeenCalledWith('p1', 's1', 'prop_1')
  })

  it('目标达成且已直接写入：不提供继续', async () => {
    const wrapper = mountPanel({
      id: 't1', status: 'completed', result: result({ outcome: 'goal_met', stop_reason: 'goal_met', message: '已达成检查目标', applied: true, unresolved: [], evidence_gaps: [] }),
    })
    await flushPromises()
    expect(wrapper.text()).toContain('已按采纳流程写入正文')
    expect(wrapper.findAll('button').some((b) => b.text().includes('继续'))).toBe(false)
  })
})
