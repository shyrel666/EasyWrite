import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { nextTick } from 'vue'
import ModeBanner from '@/components/common/ModeBanner.vue'
import { useAiStore } from '@/stores/ai'
import api from '@/api/client'

const configuredStatus = {
  llm_configured: true,
  llm_model: 'test-model',
  last_mode: null,
  embedding_available: false,
  embedding_model: '',
}

function mountBanner() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: ['/', '/settings'].map((path) => ({ path, component: { template: '<div />' } })),
  })
  const wrapper = mount(ModeBanner, {
    global: { plugins: [pinia, router], stubs: ['el-icon', 'WarningFilled'] },
  })
  return { wrapper, store: useAiStore(), router }
}

afterEach(() => vi.restoreAllMocks())

describe('AI 模式提示', () => {
  it('状态尚未加载时不显示离线提示', () => {
    const { wrapper } = mountBanner()
    expect(wrapper.find('div').exists()).toBe(false)
  })

  it('未配置生成模型时显示离线提示，并可进入设置', async () => {
    vi.spyOn(api, 'aiStatus').mockResolvedValue({ ...configuredStatus, llm_configured: false, last_mode: 'mock' })
    const { wrapper, store, router } = mountBanner()
    await store.refresh()
    expect(wrapper.text()).toContain('离线演示模式')
    expect(wrapper.text()).toContain('未配置大模型 API Key')
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/settings')
  })

  it('模型已配置但尚未生成时不显示演示提示', async () => {
    vi.spyOn(api, 'aiStatus').mockResolvedValue(configuredStatus)
    const { wrapper, store } = mountBanner()
    await store.refresh()
    expect(store.llmConfigured).toBe(true)
    expect(wrapper.find('div').exists()).toBe(false)
  })

  it('保存模型配置后的状态刷新会移除旧的离线提示', async () => {
    vi.spyOn(api, 'aiStatus')
      .mockResolvedValueOnce({ ...configuredStatus, llm_configured: false, last_mode: 'mock' })
      .mockResolvedValueOnce(configuredStatus)
    const { wrapper, store } = mountBanner()
    await store.refresh()
    expect(wrapper.text()).toContain('离线演示模式')
    await store.refresh()
    expect(wrapper.find('div').exists()).toBe(false)
  })

  it('真实调用回退时提示检查连接，不误报未配置密钥', async () => {
    vi.spyOn(api, 'aiStatus').mockResolvedValue({ ...configuredStatus, last_mode: 'mock' })
    const { wrapper, store } = mountBanner()
    await store.refresh()
    expect(wrapper.text()).toContain('上次生成已回退到演示模式')
    expect(wrapper.text()).toContain('请测试连通性后重试')
    expect(wrapper.text()).not.toContain('未配置大模型 API Key')

    store.noteMode('llm')
    await nextTick()
    expect(wrapper.find('div').exists()).toBe(false)
  })
})
