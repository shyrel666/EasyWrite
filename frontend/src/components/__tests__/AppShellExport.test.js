import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import AppShell from '@/components/layout/AppShell.vue'
import { renderOutlineDiagrams } from '@/utils/mermaid'

vi.mock('vue-router', () => ({ useRoute: () => ({ fullPath: '/', name: 'workspace' }), useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/utils/mermaid', () => ({ renderOutlineDiagrams: vi.fn().mockResolvedValue({ diagrams: [], total: 0, failed: 0 }) }))
let wrapper
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }

beforeEach(() => {
  renderOutlineDiagrams.mockResolvedValue({ diagrams: [], total: 0, failed: 0 })
  vi.spyOn(api, 'exportPreflight').mockResolvedValue({ ready: true, checks: [] })
  vi.spyOn(api, 'templates').mockResolvedValue({ templates: [{ id: 'gov_standard', name: '标准' }] })
  vi.spyOn(api, 'getProject').mockResolvedValue({ outline: [] })
  vi.spyOn(api, 'exportBid').mockResolvedValue()
  for (const kind of ['success', 'error']) vi.spyOn(ElMessage, kind).mockImplementation(() => {})
})
afterEach(() => { wrapper?.unmount(); vi.restoreAllMocks() })

function mount(beforeExport) {
  wrapper = shallowMount(AppShell, { props: { projectId: 'p1', exportEnabled: true, beforeExport }, global: {
    plugins: [createPinia()],
    stubs: {
      ElButton: { props: ['disabled', 'loading'], template: '<button :disabled="disabled || loading"><slot /></button>' },
      ElDialog: { props: ['modelValue'], template: '<div v-if="modelValue"><slot /><slot name="footer" /></div>' },
    },
    config: { warnHandler(m) { if (!m.startsWith('Failed to resolve component:')) throw new Error(m) } },
  } })
}
async function click(label) {
  await wrapper.findAll('button').find(b => b.text().trim() === label).trigger('click')
  await flushPromises()
}

it('打开检查、刷新检查及实际导出均等待保存', async () => {
  const first = deferred(), last = deferred()
  const save = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValueOnce(true).mockReturnValueOnce(last.promise)
  mount(save)
  await click('导出标书 Word')
  expect(api.exportPreflight).not.toHaveBeenCalled()
  first.resolve(true)
  await flushPromises()
  expect(api.exportPreflight).toHaveBeenCalledTimes(1)
  await click('重新检查')
  expect(save).toHaveBeenCalledTimes(2)
  await click('导出 Word')
  expect(api.getProject).not.toHaveBeenCalled()
  last.resolve(true)
  await flushPromises()
  expect(api.getProject).toHaveBeenCalledTimes(1)
  expect(api.exportBid).toHaveBeenCalledTimes(1)
})

it.each([false, 'throw'])('保存失败 %s 时停止检查和导出', async result => {
  const save = vi.fn().mockResolvedValue(true)
  mount(save)
  await click('导出标书 Word')
  if (result === 'throw') save.mockRejectedValue(new Error('磁盘不可用'))
  else save.mockResolvedValue(false)
  await click('重新检查')
  expect(api.exportPreflight).toHaveBeenCalledTimes(1)
  await click('导出 Word')
  expect(api.getProject).not.toHaveBeenCalled()
  expect(api.exportBid).not.toHaveBeenCalled()
})

it('评分索引默认开启，支持预览及关闭并传给导出接口', async () => {
  api.exportPreflight.mockResolvedValue({ ready: true, checks: [], scoring_index: [{ id: 'i1', name: '评分甲', points: 10, sections: [], attachments: [] }] })
  mount(vi.fn().mockResolvedValue(true))
  await click('导出标书 Word')
  expect(wrapper.text()).toContain('评分甲')
  expect(wrapper.get('[data-test="scoring-index"]').element.checked).toBe(true)
  await wrapper.get('[data-test="scoring-index"]').setValue(false)
  await click('导出 Word')
  expect(ElMessage.error).not.toHaveBeenCalled()
  expect(api.exportBid).toHaveBeenCalledWith('p1', 'gov_standard', [], { include_scoring_index: false, appendix_plan: [], book_kind: 'technical' })
})
