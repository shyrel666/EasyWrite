import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import KnowledgeView from '@/views/KnowledgeView.vue'
import TaskProgress from '@/components/common/TaskProgress.vue'

afterEach(() => vi.restoreAllMocks())

it('不兼容向量提示提供重建入口，完成后刷新兼容状态', async () => {
  const stats = { total_chunks: 1, documents: [{ id: 'doc1', doc_name: '资料', status: 'ready' }], dense_enabled: true,
    embedding_warning: '无兼容的向量索引，已退回 BM25 检索，请重建向量' }
  vi.spyOn(api, 'kbStats').mockResolvedValueOnce(stats).mockResolvedValue({ ...stats, embedding_warning: '' })
  vi.spyOn(api, 'listTasks').mockResolvedValue({ tasks: [] })
  vi.spyOn(api, 'kbReindex').mockResolvedValue({ task_id: 'reindex1' })
  vi.spyOn(ElMessage, 'success').mockImplementation(() => {})
  const wrapper = shallowMount(KnowledgeView, {
    global: {
      plugins: [createPinia()],
      stubs: { AppShell: { template: '<div><slot /></div>' },
        ElButton: { props: ['disabled'], template: '<button :disabled="disabled"><slot /></button>' } },
      config: { warnHandler(message) { if (!message.startsWith('Failed to resolve component:')) throw new Error(message) } },
    },
  })
  try {
    await flushPromises()
    expect(wrapper.text()).toContain('已退回 BM25')
    const rebuild = wrapper.findAll('button').find((b) => b.text() === '重建全部向量')
    await rebuild.trigger('click')
    await flushPromises()
    expect(api.kbReindex).toHaveBeenCalledOnce()
    expect(rebuild.element.disabled).toBe(true)
    expect(wrapper.getComponent(TaskProgress).props('taskId')).toBe('reindex1')
    wrapper.getComponent(TaskProgress).vm.$emit('done', { status: 'completed', result: { reembedded: 1 } })
    await flushPromises()
    expect(api.kbStats).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).not.toContain('已退回 BM25')
    expect(wrapper.findComponent(TaskProgress).exists()).toBe(false)
  } finally {
    wrapper.unmount()
  }
})
