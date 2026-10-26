import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import DeviationView from '@/views/DeviationView.vue'

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: 'p1' } }) }))
let wrapper

beforeEach(() => {
  vi.spyOn(api, 'getProject').mockResolvedValue({ id: 'p1', name: '项目', outline: [{ id: 's1', title: '方案' }] })
  vi.spyOn(api, 'getDeviations').mockResolvedValue({ items: [
    { index: 1, clause_title: '须提供证明', response_status: '待核实', response_detail: '待补证明', level: 'normal' },
  ] })
  vi.spyOn(api, 'listTasks').mockResolvedValue({ tasks: [] })
  vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm')
  vi.spyOn(window, 'open').mockImplementation(() => {})
  vi.spyOn(api, 'saveDeviations').mockResolvedValue({})
  vi.spyOn(api, 'generateDeviations').mockResolvedValue({ task_id: 't1' })
  vi.spyOn(api, 'injectDeviations').mockResolvedValue({})
  for (const kind of ['error', 'success']) vi.spyOn(ElMessage, kind).mockImplementation(() => {})
})

afterEach(() => { wrapper?.unmount(); vi.restoreAllMocks() })

async function mountView() {
  wrapper = shallowMount(DeviationView, { global: {
    plugins: [createPinia()],
    stubs: {
      AppShell: { template: '<div><slot /></div>' },
      ElButton: { props: ['disabled', 'loading'], template: '<button :disabled="disabled || loading"><slot /></button>' },
      PageHeader: { template: '<header><slot name="actions" /></header>' },
      ElPopover: { template: '<div><slot /><slot name="reference" /></div>' },
      ElInput: { props: ['modelValue'], emits: ['update:modelValue', 'input'],
        template: '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value); $emit(\'input\', $event.target.value)" />' },
      ElSelect: { props: ['modelValue'], emits: ['update:modelValue', 'change'],
        template: '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value); $emit(\'change\', $event.target.value)"><slot /></select>' },
      ElOption: { props: ['value', 'label'], template: '<option :value="value">{{ label }}</option>' },
    },
    config: { warnHandler(message) { if (!message.startsWith('Failed to resolve component:')) throw new Error(message) } },
  } })
  await flushPromises()
}

it('待核实响应保留状态并在导出前提示', async () => {
  await mountView()
  expect(wrapper.text()).toContain('待生成 / 待核实')
  expect(wrapper.findAll('option').some(o => o.attributes('value') === '待核实')).toBe(true)
  await wrapper.findAll('button').find(b => b.text().includes('导出 Word')).trigger('click')
  await flushPromises()
  expect(ElMessageBox.confirm).toHaveBeenCalledWith(expect.stringContaining('1 条尚未填写响应或待核实'), '导出前核对', expect.any(Object))
  expect(window.open).toHaveBeenCalledTimes(1)
})

async function click(label) {
  await wrapper.findAll('button').find(b => b.text().trim() === label).trigger('click')
  await flushPromises()
}

it.each(['导出 Word', 'AI 补全待生成', '回填'])('%s 等待保存，失败时停止并保留草稿', async label => {
  let reject
  api.saveDeviations.mockImplementation(() => new Promise((_, r) => { reject = r }))
  await mountView()
  await wrapper.findAll('textarea')[1].setValue('最新人工响应')
  await wrapper.find('select').setValue('s1')
  await click(label)
  expect(api.saveDeviations).toHaveBeenCalledTimes(1)
  expect(api.generateDeviations).not.toHaveBeenCalled()
  expect(api.injectDeviations).not.toHaveBeenCalled()
  expect(window.open).not.toHaveBeenCalled()
  reject(new Error('保存失败'))
  await flushPromises()
  expect(wrapper.text()).toContain('保存人工编辑')
  expect(api.generateDeviations).not.toHaveBeenCalled()
  expect(api.injectDeviations).not.toHaveBeenCalled()
  expect(window.open).not.toHaveBeenCalled()
})

it('保存途中继续输入时，先串行保存最新内容再回填', async () => {
  const resolves = []
  api.saveDeviations.mockImplementation(() => new Promise(r => { resolves.push(r) }))
  await mountView()
  await wrapper.find('select').setValue('s1')
  await wrapper.findAll('textarea')[1].setValue('第一版人工响应')
  await click('回填')
  await wrapper.findAll('textarea')[1].setValue('第二版人工响应')
  resolves[0]({})
  await flushPromises()
  expect(api.saveDeviations).toHaveBeenCalledTimes(2)
  expect(api.saveDeviations.mock.calls[0][1][0].response_detail).toBe('第一版人工响应')
  expect(api.saveDeviations.mock.calls[1][1][0].response_detail).toBe('第二版人工响应')
  expect(api.injectDeviations).not.toHaveBeenCalled()
  resolves[1]({})
  await flushPromises()
  expect(api.injectDeviations).toHaveBeenCalledWith('p1', 's1')
})
