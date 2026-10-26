import { expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import VerificationList from '@/components/evidence/VerificationList.vue'
import CheckReport from '@/components/workspace/CheckReport.vue'
import api from '@/api/client'

const item = { stable_key: 'asset:p', basis_hash: 'old', note: '待核实人员', decision_state: 'stale', occurrences: [
  { section_id: 'a', section_title: '甲章', line: 1, text: '王工' },
  { section_id: 'b', section_title: '乙章', line: 3, text: '王工' },
] }

it('事项数与出现次数分别展示，定位保留所有章节', async () => {
  const w = mount(VerificationList, { props: { projectId: 'p', items: [item] } })
  expect(w.text()).toContain('1 项 / 2 处')
  expect(w.text()).toContain('原核实结论失效')
  const positions = w.findAll('li button')
  await positions[1].trigger('click')
  expect(w.emitted('locate')[0]).toEqual(['b'])
  const save = vi.spyOn(api, 'confirmVerification').mockRejectedValue(new Error('事项或依据已变化'))
  await w.get('input').setValue('核实依据')
  await w.findAll('button').at(-1).trigger('click')
  await flushPromises()
  expect(save).toHaveBeenCalledWith('p', 'asset:p', { basis_hash: 'old', note: '核实依据' })
  expect(w.text()).toContain('事项或依据已变化')
  expect(w.emitted('changed')).toBeUndefined()
  save.mockRestore()
  w.unmount()
})

it('章节报告显示全部行号并发出相应定位', async () => {
  const w = mount(CheckReport, { props: { locatable: true, report: { passed: true, quality_count: 0, points: [], issues: [], pending_verification: [item], verification_count: 1, verification_occurrences: 2 } } })
  expect(w.text()).toContain('1 项 / 2 处')
  const button = w.findAll('button').find(b => b.text().includes('第 3 行'))
  await button.trigger('click')
  expect(w.emitted('locate')[0][0].line).toBe(3)
  w.unmount()
})
