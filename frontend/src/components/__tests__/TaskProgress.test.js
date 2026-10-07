import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'
import TaskProgress from '@/components/common/TaskProgress.vue'
import api from '@/api/client'

function mountProgress(props = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  return mount(TaskProgress, {
    props: { taskId: 't1', title: '正在测试', ...props },
    global: { plugins: [pinia, ElementPlus] },
  })
}

describe('TaskProgress', () => {
  beforeEach(() => {
    vi.spyOn(api, 'listTasks').mockResolvedValue({ tasks: [] })
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('完成后回传 done 并显示 100%', async () => {
    vi.spyOn(api, 'pollTask').mockImplementation(async (id, onProgress) => {
      onProgress({ status: 'running', progress: 40, message: '撰写 2/5' })
      return { status: 'completed', progress: 100, message: '完成', result: { ok: true } }
    })
    const wrapper = mountProgress()
    await flushPromises()
    expect(wrapper.emitted('done')[0][0].result).toEqual({ ok: true })
    expect(wrapper.emitted('failed')).toBeUndefined()
    expect(wrapper.text()).toContain('100%')
  })

  it('服务重启导致的中断：显示"已中断"与原因，并回传 failed', async () => {
    const interrupted = { status: 'interrupted', progress: 30, message: '服务在任务完成前重启，任务已中断', error: '服务在任务完成前重启，任务已中断' }
    vi.spyOn(api, 'pollTask').mockResolvedValue(interrupted)
    const wrapper = mountProgress()
    await flushPromises()
    expect(wrapper.text()).toContain('已中断')
    expect(wrapper.text()).toContain('服务在任务完成前重启')
    expect(wrapper.emitted('failed')[0][0]).toEqual(interrupted)
    expect(wrapper.emitted('done')).toBeUndefined()
  })

  it('连接中断期间提示等待服务恢复', async () => {
    let finish
    vi.spyOn(api, 'pollTask').mockImplementation((id, onProgress) => new Promise((resolve) => {
      onProgress({ status: 'running', progress: 20, message: '解析中', reconnecting: true })
      finish = resolve
    }))
    const wrapper = mountProgress({ cancellable: true })
    await flushPromises()
    expect(wrapper.text()).toContain('正在等待服务恢复')
    expect(wrapper.text()).not.toContain('取消') // 断线时不提供取消
    finish({ status: 'completed', progress: 100, message: '完成' })
    await flushPromises()
    expect(wrapper.emitted('done')).toHaveLength(1)
  })

  it('卸载时中止轮询，不再回传事件', async () => {
    let signal
    vi.spyOn(api, 'pollTask').mockImplementation((id, onProgress, opts) => {
      signal = opts.signal
      return new Promise((resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      })
    })
    const wrapper = mountProgress()
    await flushPromises()
    wrapper.unmount()
    await flushPromises()
    expect(signal.aborted).toBe(true)
    expect(wrapper.emitted('failed')).toBeUndefined()
  })
})
