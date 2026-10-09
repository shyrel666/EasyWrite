import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import api from '@/api/client'

function jsonResponse(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body }
}

it('流式请求建立前发生 409，向编辑器透传正文冲突详情', async () => {
  const detail = { reason: 'content_changed', message: '正文已变化', current_content: '人工稿', current_revision: 2 }
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail }, 409)))
  const onClose = vi.fn()
  try {
    const error = await new Promise((resolve) => api.streamSection('p1', {}, vi.fn(), resolve, onClose))
    expect(error).toMatchObject({ message: '正文已变化', status: 409, detail })
    expect(onClose).toHaveBeenCalledOnce()
  } finally {
    vi.unstubAllGlobals()
  }
})

const running = (progress) => ({ id: 't1', status: 'running', progress, message: `进度 ${progress}` })

describe('pollTask', () => {
  let fetchMock

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('轮询到终态为止，interrupted（服务重启中断）也是终态', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(running(10)))
      .mockResolvedValueOnce(jsonResponse(running(50)))
      .mockResolvedValueOnce(jsonResponse({ id: 't1', status: 'interrupted', progress: 50, error: '服务重启' }))
    const seen = []
    const task = await api.pollTask('t1', (t) => seen.push(t.progress), { intervalMs: 0 })
    expect(task.status).toBe('interrupted')
    expect(seen).toEqual([10, 50, 50])
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/tasks/t1', expect.objectContaining({ method: 'GET' }))
  })

  it('服务重启期间的连接失败与网关错误：标记 reconnecting 后继续等待', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(running(30)))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(jsonResponse({ detail: 'Bad Gateway' }, 502))
      .mockResolvedValueOnce(jsonResponse({ id: 't1', status: 'interrupted', progress: 30 }))
    const seen = []
    const task = await api.pollTask('t1', (t) => seen.push(t), { intervalMs: 0, retryMs: 0 })
    expect(task.status).toBe('interrupted')
    expect(seen.map((t) => !!t.reconnecting)).toEqual([false, true, true, false])
    expect(seen[1].progress).toBe(30) // 重连期间保留最后一次进度
  })

  it('任务不存在（404）立即报错，不重试', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: '任务不存在' }, 404))
    await expect(api.pollTask('nope', null, { intervalMs: 0, retryMs: 0 })).rejects.toThrow('任务不存在')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('连续失败超过上限后放弃', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(api.pollTask('t1', null, { retryMs: 0, maxFailures: 3 })).rejects.toThrow('Failed to fetch')
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it('signal 中止后停止轮询', async () => {
    const controller = new AbortController()
    fetchMock.mockImplementation(async () => {
      controller.abort()
      return jsonResponse(running(5))
    })
    await expect(api.pollTask('t1', null, { intervalMs: 0, signal: controller.signal }))
      .rejects.toMatchObject({ name: 'AbortError' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

describe('任务列表与调用记录查询参数', () => {
  let fetchMock

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(jsonResponse({ tasks: [] }))
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('listTasks 只带传入的筛选条件', async () => {
    await api.listTasks()
    await api.listTasks({ projectId: 'p1', type: 'section_batch', active: true, limit: 1 })
    await api.listTasks({ projectId: '' })
    const urls = fetchMock.mock.calls.map((c) => c[0])
    expect(urls[0]).toBe('/api/v1/tasks')
    expect(urls[1]).toBe('/api/v1/tasks?project_id=p1&type=section_batch&active=true&limit=1')
    expect(urls[2]).toBe('/api/v1/tasks?project_id=')
  })

  it('latestTask 取最近一条（含结果），没有则为 null', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ tasks: [{ id: 't9', result: { ok: 1 } }] }))
    expect(await api.latestTask('p1', 'compliance_check')).toEqual({ id: 't9', result: { ok: 1 } })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/tasks?project_id=p1&type=compliance_check&with_result=true&limit=1')
    expect(await api.latestTask('p1', 'compliance_check')).toBeNull()
  })

  it('llmUsage：不筛选 / 只看未归属项目 / 指定项目', async () => {
    await api.llmUsage()
    await api.llmUsage({ days: 30, projectId: '' })
    await api.llmUsage({ days: 1, projectId: 'p1' })
    const urls = fetchMock.mock.calls.map((c) => c[0])
    expect(urls).toEqual([
      '/api/v1/ai/usage?days=7',
      '/api/v1/ai/usage?days=30&project_id=',
      '/api/v1/ai/usage?days=1&project_id=p1',
    ])
  })
})

describe('health', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('探针在站点根路径 /health，不带接口前缀', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ status: 'ok', version: '0.2.0' }))
    vi.stubGlobal('fetch', fetchMock)
    expect(await api.health()).toEqual({ status: 'ok', version: '0.2.0' })
    expect(fetchMock).toHaveBeenCalledWith('/health', expect.objectContaining({ method: 'GET' }))
  })
})

describe('request errors', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('结构化 detail（候选稿 409）：message 取 detail.message，完整内容在 err.detail', async () => {
    const detail = { reason: 'content_changed', message: '正文已变化', current_content: '新正文' }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail }, 409)))
    const err = await api.applyProposal('p1', 's1', 'prop_1').catch((e) => e)
    expect(err.message).toBe('正文已变化')
    expect(err.status).toBe(409)
    expect(err.detail).toEqual(detail)
  })

  it('字符串 detail 原样作为 message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: '项目不存在' }, 404)))
    const err = await api.getProject('x').catch((e) => e)
    expect(err.message).toBe('项目不存在')
    expect(err.detail).toBe('项目不存在')
  })
})
