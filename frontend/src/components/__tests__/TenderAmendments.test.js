import { expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import TenderAmendments from '@/components/workspace/TenderAmendments.vue'
import api from '@/api/client'
vi.mock('@/api/client', () => ({ default: { tenderVersions: vi.fn(), decideOverride: vi.fn() } }))

it('显示原文、影响章节及待确认状态，只有明确确认才写入', async () => {
  api.tenderVersions.mockResolvedValue({ package: '包1', documents: [], clauses: [{ id: 's1', label: '服务', text: '旧要求', original_text: '原条款', sections: [{ id: 'n', title: '运维章' }] }], overrides: [{ id: 'o', state: 'pending', text: '新要求', original_text: '旧要求', basis_hash: 'sha', source_page: 2 }] })
  const w = mount(TenderAmendments, { props: { projectId: 'p' } })
  w.get('details').element.open = true
  await w.get('details').trigger('toggle'); await flushPromises()
  await w.get('[aria-label="原条款"]').setValue('s1')
  expect(w.text()).toContain('影响章节：运维章')
  expect(w.text()).toContain('待确认（尚未生效）')
  expect(api.decideOverride).not.toHaveBeenCalled()
  await w.findAll('button').find(b => b.text() === '核对后确认生效').trigger('click'); await flushPromises()
  expect(api.decideOverride).toHaveBeenCalledWith('p', 'o', { state: 'confirmed', basis_hash: 'sha' })
  await w.findAll('button').find(b => b.text() === '撤回').trigger('click'); await flushPromises()
  expect(api.decideOverride).toHaveBeenLastCalledWith('p', 'o', { state: 'withdrawn', basis_hash: 'sha' })
})
