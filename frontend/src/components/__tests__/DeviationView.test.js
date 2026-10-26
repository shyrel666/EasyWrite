import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { ElMessageBox } from 'element-plus'
import api from '@/api/client'
import DeviationView from '@/views/DeviationView.vue'

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: 'p1' } }) }))
let wrapper

beforeEach(() => {
  vi.spyOn(api, 'getProject').mockResolvedValue({ id: 'p1', name: '项目', outline: [] })
  vi.spyOn(api, 'getDeviations').mockResolvedValue({ items: [
    { index: 1, clause_title: '须提供证明', response_status: '待核实', response_detail: '待补证明', level: 'normal' },
  ] })
  vi.spyOn(api, 'listTasks').mockResolvedValue({ tasks: [] })
  vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm')
  vi.spyOn(window, 'open').mockImplementation(() => {})
})

afterEach(() => { wrapper?.unmount(); vi.restoreAllMocks() })

async function mountView() {
  wrapper = shallowMount(DeviationView, { global: {
    plugins: [createPinia()],
    stubs: {
      AppShell: { template: '<div><slot /></div>' },
      ElButton: { props: ['disabled', 'loading'], template: '<button :disabled="disabled || loading"><slot /></button>' },
    },
    config: { warnHandler(message) { if (!message.startsWith('Failed to resolve component:')) throw new Error(message) } },
  } })
  await flushPromises()
}

it('待核实响应保留状态并在导出前提示', async () => {
  await mountView()
  expect(wrapper.text()).toContain('待生成 / 待核实')
  expect(wrapper.findAll('el-option').some(o => o.attributes('value') === '待核实')).toBe(true)
  await wrapper.findAll('button').find(b => b.text().includes('导出 Word')).trigger('click')
  await flushPromises()
  expect(ElMessageBox.confirm).toHaveBeenCalledWith(expect.stringContaining('1 条尚未填写响应或待核实'), '导出前核对', expect.any(Object))
  expect(window.open).toHaveBeenCalledTimes(1)
})
