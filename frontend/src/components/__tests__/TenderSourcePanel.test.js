import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import api from '@/api/client'
import TenderSourcePanel from '@/components/workspace/TenderSourcePanel.vue'

afterEach(() => vi.restoreAllMocks())

const empty = { has_structure: false, sections: [], keywords: [], related: [] }

it.each([
  ['text', '来自粘贴文本'],
  ['', '没有存档的招标文件章节'],
])('无章节树时按原文来源 %j 说明原因', async (source, hint) => {
  vi.spyOn(api, 'tenderOutline').mockResolvedValue({ ...empty, source })
  const wrapper = shallowMount(TenderSourcePanel, {
    props: { projectId: 'p1', node: null },
    global: { config: { warnHandler(message) { if (!message.startsWith('Failed to resolve component:')) throw new Error(message) } } },
  })
  await flushPromises()
  expect(wrapper.text()).toContain(hint)
  wrapper.unmount()
})
