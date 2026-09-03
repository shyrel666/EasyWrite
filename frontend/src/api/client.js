/**
 * 统一 API 客户端：
 * - request(): JSON 请求 + 统一错误抛出（后端 detail 透传）
 * - uploadFile(): FormData 上传
 * - streamSSE(): SSE 流式读取（章节生成打字机）
 * - pollTask(): 后台任务轮询（入库/拆标/合规/偏离表批量）
 */
const BASE = '/api/v1'

async function request(path, { method = 'GET', body, formData, signal } = {}) {
  const opts = { method, signal, headers: {} }
  if (formData) {
    opts.body = formData
  } else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(`${BASE}${path}`, opts)
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const data = await res.json()
      detail = data.detail || JSON.stringify(data)
    } catch { /* ignore */ }
    const err = new Error(detail)
    err.status = res.status
    throw err
  }
  return res.json()
}

function uploadFile(path, file, extra = {}) {
  const fd = new FormData()
  fd.append('file', file)
  for (const [k, v] of Object.entries(extra)) fd.append(k, v)
  return request(path, { method: 'POST', formData: fd })
}

/**
 * SSE 流式读取。onEvent(data) 收到每个解析后的 JSON 事件。
 * 返回 abort 函数。
 */
function streamSSE(path, body, onEvent, onError) {
  const controller = new AbortController()
  ;(async () => {
    try {
      const res = await fetch(`${BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      })
      if (!res.ok || !res.body) {
        let detail = `HTTP ${res.status}`
        try { detail = (await res.json()).detail || detail } catch { /* */ }
        throw new Error(detail)
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n\n')
        buffer = lines.pop() || ''
        for (const line of lines) {
          const dataLine = line.split('\n').find((l) => l.startsWith('data: '))
          if (dataLine) {
            try {
              onEvent(JSON.parse(dataLine.slice(6)))
            } catch { /* 忽略无法解析的帧 */ }
          }
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError' && onError) onError(e)
    }
  })()
  return () => controller.abort()
}

/**
 * 后台任务轮询，直到 completed/failed/cancelled。
 * onProgress(task) 每次收到状态回调；返回最终 task 对象。
 */
async function pollTask(taskId, onProgress, intervalMs = 800) {
  for (;;) {
    const task = await request(`/tasks/${taskId}`)
    if (onProgress) onProgress(task)
    if (['completed', 'failed', 'cancelled'].includes(task.status)) return task
    await new Promise((r) => setTimeout(r, intervalMs))
  }
}

export const api = {
  // 健康 & AI 状态
  health: () => request('/../health'),
  aiStatus: () => request('/ai/status'),
  getAiSettings: () => request('/ai/settings'),
  updateAiSettings: (data) => request('/ai/settings', { method: 'PUT', body: data }),
  testAi: (data) => request('/ai/test', { method: 'POST', body: data }),
  fetchModels: (data) => request('/ai/models/fetch', { method: 'POST', body: data }),
  embeddingDescribe: () => request('/ai/embedding/describe'),
  testEmbedding: () => request('/ai/embedding/test', { method: 'POST' }),

  // 项目
  listProjects: () => request('/projects'),
  createProject: (data) => request('/project/create', { method: 'POST', body: data }),
  getProject: (id) => request(`/project/${id}`),
  deleteProject: (id) => request(`/project/${id}`, { method: 'DELETE' }),
  updateFacts: (id, facts) => request(`/project/${id}/facts`, { method: 'PUT', body: facts }),
  updateOutline: (id, outline) => request(`/project/${id}/outline`, { method: 'PUT', body: { outline } }),
  updateStage: (id, stage) => request(`/project/${id}/stage`, { method: 'PUT', body: { stage } }),
  updateSectionRefs: (id, sectionId, pinnedRefs, excludedRefs) =>
    request(`/project/${id}/section/refs`, { method: 'PUT', body: { section_id: sectionId, pinned_refs: pinnedRefs, excluded_refs: excludedRefs } }),

  // 招标解析
  analyzeTender: (file) => uploadFile('/tender/analyze', file),
  analyzeTenderText: (text) => request('/tender/analyze/text', { method: 'POST', body: { text } }),
  applyTender: (id, analysis, tenderText) =>
    request(`/project/${id}/tender/apply`, { method: 'POST', body: { analysis, tender_text: tenderText } }),

  // 大纲
  draftLevel1: (id) => request(`/project/${id}/outline/draft-level1`, { method: 'POST', body: {} }),
  expandOutline: (id, chapters, totalWordBudget) =>
    request(`/project/${id}/outline/expand`, { method: 'POST', body: { chapters, total_word_budget: totalWordBudget } }),

  // 章节
  generateSection: (id, payload) => request(`/project/${id}/section/generate`, { method: 'POST', body: payload }),
  saveSection: (id, sectionId, content, status) =>
    request(`/project/${id}/section`, { method: 'PUT', body: { section_id: sectionId, content, status } }),
  polishSection: (id, payload) => request(`/project/${id}/section/polish`, { method: 'POST', body: payload }),
  streamSection: (id, payload, onEvent, onError) => streamSSE(`/project/${id}/section/generate/stream`, payload, onEvent, onError),

  // 偏离表
  extractDeviations: (id) => request(`/project/${id}/deviation/extract`, { method: 'POST', body: {} }),
  getDeviations: (id) => request(`/project/${id}/deviation`),
  saveDeviations: (id, items) => request(`/project/${id}/deviation`, { method: 'PUT', body: items }),
  generateDeviations: (id) => request(`/project/${id}/deviation/generate`, { method: 'POST' }),
  injectDeviations: (id, sectionId) =>
    request(`/project/${id}/deviation/inject`, { method: 'POST', body: { section_id: sectionId } }),

  // 合规质检
  complianceCheck: (id) => request(`/project/${id}/compliance/check`, { method: 'POST' }),
  qualityInspect: (id) => request(`/project/${id}/quality/inspect`, { method: 'POST' }),

  // 知识库
  kbUpload: (file, curate) => uploadFile('/knowledge/upload', file, { curate }),
  kbDocuments: () => request('/knowledge/documents'),
  kbDelete: (docId) => request(`/knowledge/documents/${docId}`, { method: 'DELETE' }),
  kbReindex: (docId) => request(`/knowledge/documents/${docId}/reindex`, { method: 'POST' }),
  kbCurate: (docId) => request(`/knowledge/documents/${docId}/curate`, { method: 'POST' }),
  kbStats: () => request('/knowledge/stats'),
  kbSearch: (payload) => request('/knowledge/search', { method: 'POST', body: payload }),
  kbItems: (docId) => request(`/knowledge/items${docId ? `?doc_id=${docId}` : ''}`),

  // 资产
  assets: {
    stats: () => request('/assets/stats'),
    list: (type, params = '') => request(`/assets/${type}${params}`),
    add: (type, body) => request(`/assets/${type}`, { method: 'POST', body }),
    remove: (type, id) => request(`/assets/${type}/${id}`, { method: 'DELETE' }),
  },

  // 模板与导出
  templates: () => request('/templates/list'),
  createTemplate: (data) => request('/templates/create', { method: 'POST', body: data }),
  exportUrl: (id, templateId) => `${BASE}/project/${id}/export?template_id=${templateId || 'gov_standard'}`,

  // 任务
  pollTask,
}

export default api
