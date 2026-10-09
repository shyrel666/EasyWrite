import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import WizardView from '@/views/WizardView.vue'
import TaskProgress from '@/components/common/TaskProgress.vue'

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: 'p1' } }),
  useRouter: () => ({ push: vi.fn() }),
}))

let wrapper

beforeEach(() => {
  vi.spyOn(api, 'getProject').mockResolvedValue({ id: 'p1', name: '项目', outline: [], facts: {}, stage: 'created' })
  vi.spyOn(api, 'latestTask').mockResolvedValue(null)
  vi.spyOn(api, 'applyTender').mockResolvedValue({ stage: 'analyzed', star_count: 0 })
  vi.spyOn(ElMessage, 'success').mockImplementation(() => {})
})

afterEach(() => {
  wrapper?.unmount()
  vi.restoreAllMocks()
})

async function mountWizard() {
  wrapper = shallowMount(WizardView, {
    global: {
      plugins: [createPinia()],
      stubs: {
        AppShell: { template: '<div><slot /></div>' },
        ElButton: { props: ['disabled', 'loading'], template: '<button :disabled="disabled || loading"><slot /></button>' },
        ElInput: {
          props: ['modelValue'],
          emits: ['update:modelValue'],
          template: '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
        },
      },
      config: { warnHandler(message) { if (!message.startsWith('Failed to resolve component:')) throw new Error(message) } },
    },
  })
  await flushPromises()
}

async function click(label) {
  const button = wrapper.findAll('button').find((b) => b.text().includes(label))
  expect(button, `找不到按钮：${label}`).toBeDefined()
  await button.trigger('click')
  await flushPromises()
}

it('分析期间继续编辑输入时，保存分析实际使用的原文', async () => {
  let resolveAnalysis
  vi.spyOn(api, 'analyzeTenderText').mockImplementation(() => new Promise((resolve) => { resolveAnalysis = resolve }))
  await mountWizard()
  await click('粘贴正文文本')
  const originalText = '甲项目技术要求：系统必须提供容灾备份。'.repeat(4)
  const editedText = '乙项目技术要求：系统必须提供审计功能。'.repeat(4)
  await wrapper.get('textarea').setValue(originalText)
  await click('执行 18 项拆标')
  await wrapper.get('textarea').setValue(editedText)
  resolveAnalysis({ project_name: '甲项目', scoring_items: [] })
  await flushPromises()
  expect(api.analyzeTenderText).toHaveBeenCalledWith(originalText)
  expect(api.applyTender).toHaveBeenCalledWith('p1', expect.objectContaining({ project_name: '甲项目' }), originalText)
})

it('粘贴、上传文件再粘贴的流程只提交与当前分析对应的原文', async () => {
  vi.spyOn(api, 'analyzeTenderText')
    .mockResolvedValueOnce({ project_name: '粘贴甲', scoring_items: [] })
    .mockResolvedValueOnce({ project_name: '粘贴丙', scoring_items: [] })
  vi.spyOn(api, 'analyzeTender').mockResolvedValue({ task_id: 'file-task' })
  await mountWizard()
  await click('粘贴正文文本')
  const oldText = '甲项目技术要求：系统必须提供容灾备份。'.repeat(4)
  await wrapper.get('textarea').setValue(oldText)
  await click('执行 18 项拆标')
  expect(api.applyTender).toHaveBeenLastCalledWith('p1', expect.objectContaining({ project_name: '粘贴甲' }), oldText)

  await click('返回上传')
  const file = new File(['招标乙'], '招标乙.docx')
  const input = wrapper.get('input[type="file"]')
  Object.defineProperty(input.element, 'files', { value: [file] })
  await input.trigger('change')
  await flushPromises()
  expect(api.analyzeTender).toHaveBeenCalledWith(file, 'p1')
  wrapper.getComponent(TaskProgress).vm.$emit('done', {
    status: 'completed', result: { project_name: '文件乙', scoring_items: [] },
  })
  await flushPromises()
  await click('查看拆标结果')
  await click('仅保存')
  expect(api.applyTender).toHaveBeenLastCalledWith('p1', expect.objectContaining({ project_name: '文件乙' }), '')

  await click('上一步')
  await click('重新上传')
  await click('粘贴正文文本')
  const newText = '丙项目技术要求：系统必须提供监控告警。'.repeat(4)
  await wrapper.get('textarea').setValue(newText)
  await click('执行 18 项拆标')
  expect(api.applyTender).toHaveBeenLastCalledWith('p1', expect.objectContaining({ project_name: '粘贴丙' }), newText)
  expect(api.applyTender).toHaveBeenCalledTimes(3)
})
