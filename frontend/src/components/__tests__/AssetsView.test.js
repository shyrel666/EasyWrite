import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import AssetsView from '@/views/AssetsView.vue'

let wrapper
const row = { id: 'q1', name: '真实企业资质', cert_no: 'C1', issue_org: '机构', status: 'example', attachments: [] }
const audit = { total: 1, items: [{ kind: 'qualifications', asset_id: 'q1', name: row.name, changed_fields: ['name'] }] }

beforeEach(() => {
  vi.spyOn(api.assets, 'list').mockResolvedValue([row])
  vi.spyOn(api.assets, 'stats').mockResolvedValue({ total_examples: 1 })
  vi.spyOn(api.assets, 'migrationAudit').mockResolvedValue(audit)
  vi.spyOn(api.assets, 'materialCheck').mockResolvedValue({ checks: [] })
  vi.spyOn(api.assets, 'add').mockResolvedValue({})
  vi.spyOn(api.assets, 'clearExamples').mockResolvedValue({ protected: audit.items })
  vi.spyOn(api, 'listProjects').mockResolvedValue([])
  vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm')
  for (const kind of ['error', 'success', 'warning']) vi.spyOn(ElMessage, kind).mockImplementation(() => {})
})
afterEach(() => { wrapper?.unmount(); vi.restoreAllMocks() })

async function mount() {
  const slot = { template: '<div><slot /></div>' }
  wrapper = shallowMount(AssetsView, { global: { stubs: {
    AppShell: slot, ElForm: slot, ElFormItem: slot,
    ElDialog: { props: ['modelValue'], template: '<div v-if="modelValue"><slot /><slot name="footer" /></div>' },
    ElButton: { template: '<button><slot /></button>' },
    ElSelect: { props: ['modelValue'], emits: ['update:modelValue'],
      template: '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><slot /></select>' },
    ElOption: { props: ['value', 'label'], template: '<option :value="value">{{ label }}</option>' },
  }, config: { warnHandler(m) { if (!m.startsWith('Failed to resolve component:')) throw new Error(m) } } } })
  await flushPromises()
}
async function click(label) {
  await wrapper.findAll('button').find(b => b.text().includes(label)).trigger('click')
  await flushPromises()
}

it('展示可疑示例并打开人工核对，读取不会自动确认资料', async () => {
  await mount()
  expect(wrapper.text()).toContain('1 条历史示例存在业务修改')
  expect(api.assets.add).not.toHaveBeenCalled()
  await click('核对 真实企业资质')
  const status = wrapper.findAll('select').find(s => s.find('option[value="unverified"]').exists())
  expect(status.element.value).toBe('example')
  await status.setValue('unverified')
  await click('保存')
  expect(api.assets.add).toHaveBeenCalledWith('qualifications', expect.objectContaining({ id: 'q1', status: 'unverified' }))
})

it('清除操作明确提示保留待核对条目及附件', async () => {
  await mount()
  await click('清除示例资料')
  expect(ElMessageBox.confirm).toHaveBeenCalledWith(expect.stringContaining('附件将保留'), '清除示例资料', expect.any(Object))
  expect(ElMessage.warning).toHaveBeenCalledWith('已清除未修改的示例，保留 1 条需核对资料')
})
