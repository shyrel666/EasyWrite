import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useTaskStore } from '@/stores/tasks'
import api from '@/api/client'

describe('任务中心 store', () => {
  let listTasks

  beforeEach(() => {
    vi.useFakeTimers()
    setActivePinia(createPinia())
    listTasks = vi.spyOn(api, 'listTasks')
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('有进行中任务时低频刷新，全部结束后停止', async () => {
    listTasks
      .mockResolvedValueOnce({ tasks: [{ id: 'a', status: 'running' }] })
      .mockResolvedValueOnce({ tasks: [{ id: 'a', status: 'interrupted' }] })
    const store = useTaskStore()
    store.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(store.activeCount).toBe(1)
    await vi.advanceTimersByTimeAsync(5000)
    expect(listTasks).toHaveBeenCalledTimes(2)
    expect(store.activeCount).toBe(0)
    await vi.advanceTimersByTimeAsync(60000)
    expect(listTasks).toHaveBeenCalledTimes(2)
  })

  it('两个入口共用一个定时器；面板打开时高频刷新', async () => {
    listTasks.mockResolvedValue({ tasks: [] })
    const store = useTaskStore()
    store.start()
    store.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(listTasks).toHaveBeenCalledTimes(1)

    store.setPanelOpen(true)
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(1500)
    expect(listTasks).toHaveBeenCalledTimes(3)

    store.setPanelOpen(false)
    await vi.advanceTimersByTimeAsync(10000)
    expect(listTasks).toHaveBeenCalledTimes(3)
  })

  it('最后一个入口卸载后不再轮询', async () => {
    listTasks.mockResolvedValue({ tasks: [{ id: 'a', status: 'running' }] })
    const store = useTaskStore()
    store.start()
    await vi.advanceTimersByTimeAsync(0)
    store.stop()
    await vi.advanceTimersByTimeAsync(30000)
    expect(listTasks).toHaveBeenCalledTimes(1)
  })

  it('加载失败时记录错误', async () => {
    listTasks.mockRejectedValue(new Error('HTTP 500'))
    const store = useTaskStore()
    store.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(store.error).toBe('HTTP 500')
  })
})
