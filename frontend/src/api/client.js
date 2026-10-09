/**
 * 统一 API 客户端：
 * - request(): JSON 请求 + 统一错误抛出（后端 detail 透传）
 * - uploadFile(): FormData 上传
 * - streamSSE(): SSE 流式读取（章节生成打字机）
 * - pollTask(): 后台任务轮询（入库/拆标/合规/偏离表批量）
 */
import { TERMINAL_STATUSES } from '@/utils/tasks'

const BASE = '/api/v1'

// base：接口前缀；站点根路径的探针（/health）传空串
async function request(path, { method = 'GET', body, formData, signal, base = BASE } = {}) {
  const opts = { method, signal, headers: {} }
  if (formData) {
    opts.body = formData
  } else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(`${base}${path}`, opts)
  if (!res.ok) {
    let message = `HTTP ${res.status}`
    let detail = null
    try {
      const data = await res.json()
      detail = data.detail ?? null
      // 结构化错误（如候选稿采纳的 409：{reason, message, …}）取其 message，完整内容放在 err.detail
      if (detail && typeof detail === 'object' && !Array.isArray(detail)) message = detail.message || JSON.stringify(detail)
      else message = detail || JSON.stringify(data)
    } catch { /* ignore */ }
    const err = new Error(message)
    err.status = res.status
    err.detail = detail
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
 * onClose() 在流结束、出错或被中止后调用（用于复位"生成中"状态）。
 * 立即返回 abort 函数（流在后台继续读取）。
 */
function streamSSE(path, body, onEvent, onError, onClose) {
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
        const error = new Error(typeof detail === 'object' ? detail.message || JSON.stringify(detail) : detail)
        error.status = res.status
        error.detail = detail
        throw error
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
    } finally {
      if (onClose) onClose()
    }
  })()
  return () => controller.abort()
}

/**
 * POST JSON 并把返回的文件下载到本地（文件名取自 Content-Disposition）。
 */
async function downloadPost(path, body, fallbackName = 'download.docx') {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try { detail = (await res.json()).detail || detail } catch { /* */ }
    throw new Error(detail)
  }
  const disposition = res.headers.get('Content-Disposition') || ''
  const star = disposition.match(/filename\*=(?:UTF-8|utf-8)''([^;]+)/)
  const plain = disposition.match(/filename="?([^";]+)"?/)
  const filename = star ? decodeURIComponent(star[1]) : (plain ? plain[1] : fallbackName)
  const url = URL.createObjectURL(await res.blob())
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10000)
  return filename
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/**
 * 后台任务轮询，直到 completed/failed/cancelled/interrupted。
 * onProgress(task) 每次收到状态回调；返回最终 task 对象。
 * 服务重启期间连接失败或网关报错时继续等待（回调的 task 带 reconnecting: true），
 * 恢复后读到的是落库的任务记录——重启前未跑完的任务状态为 interrupted（已中断）。
 * signal 中止后停止轮询并抛出 AbortError（组件卸载时使用）。
 */
async function pollTask(taskId, onProgress, { intervalMs = 800, retryMs = 1500, maxFailures = 40, signal } = {}) {
  let last = null
  let failures = 0
  for (;;) {
    let task
    try {
      task = await request(`/tasks/${taskId}`, { signal })
      failures = 0
    } catch (e) {
      if (e.name === 'AbortError') throw e
      const transient = !e.status || e.status >= 500
      if (!transient || ++failures > maxFailures) throw e
      if (onProgress && last) onProgress({ ...last, reconnecting: true })
      await sleep(retryMs)
      if (signal?.aborted) throw new DOMException('轮询已中止', 'AbortError')
      continue
    }
    last = task
    if (onProgress) onProgress(task)
    if (TERMINAL_STATUSES.includes(task.status)) return task
    await sleep(intervalMs)
    if (signal?.aborted) throw new DOMException('轮询已中止', 'AbortError')
  }
}

/** 最近任务：{ projectId, type, active, withResult, limit } */
function listTasks({ projectId, type, active, withResult, limit } = {}) {
  const params = new URLSearchParams()
  if (projectId !== undefined) params.set('project_id', projectId)
  if (type) params.set('type', type)
  if (active) params.set('active', 'true')
  if (withResult) params.set('with_result', 'true')
  if (limit) params.set('limit', String(limit))
  const qs = params.toString()
  return request(`/tasks${qs ? `?${qs}` : ''}`)
}

/** 某项目某类任务的最近一条（含结果），没有则返回 null */
async function latestTask(projectId, type) {
  const { tasks } = await listTasks({ projectId, type, withResult: true, limit: 1 })
  return tasks[0] || null
}

export const api = {
  // 健康 & AI 状态
  health: () => request('/health', { base: '' }),
  aiStatus: () => request('/ai/status'),
  getAiSettings: () => request('/ai/settings'),
  updateAiSettings: (data) => request('/ai/settings', { method: 'PUT', body: data }),
  testAi: (data) => request('/ai/test', { method: 'POST', body: data }),
  fetchModels: (data) => request('/ai/models/fetch', { method: 'POST', body: data }),
  embeddingDescribe: () => request('/ai/embedding/describe'),
  testEmbedding: () => request('/ai/embedding/test', { method: 'POST' }),
  // projectId：undefined 不筛选，'' 只看未归属项目的调用（知识库入库、连接测试等）；runId 只看某次运行（后台任务）
  llmUsage: ({ days = 7, projectId, runId } = {}) => {
    const params = new URLSearchParams({ days: String(days) })
    if (projectId !== undefined) params.set('project_id', projectId)
    if (runId) params.set('run_id', runId)
    return request(`/ai/usage?${params}`)
  },

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
  // 传入 projectId 时招标原文随上传存档到项目（偏离表抽取 / 合规核查依据）
  analyzeTender: (file, projectId) => uploadFile('/tender/analyze', file, projectId ? { project_id: projectId } : {}),
  analyzeTenderText: (text) => request('/tender/analyze/text', { method: 'POST', body: { text } }),
  applyTender: (id, analysis, tenderText) =>
    request(`/project/${id}/tender/apply`, { method: 'POST', body: { analysis, tender_text: tenderText } }),
  // 承诺建议只复述招标要求，用户采纳后经 updateFacts 写入全局事实
  commitmentSuggestions: (id) => request(`/project/${id}/tender/commitment-suggestions`),
  // 招标原文：章节树（传 sectionId 时附带该章节的要点与相关原文章节）、按章节分段读取
  tenderOutline: (id, sectionId) =>
    request(`/project/${id}/tender/outline${sectionId ? `?section_id=${encodeURIComponent(sectionId)}` : ''}`),
  tenderSection: (id, path, offset = 0, limit = 20000) =>
    request(`/project/${id}/tender/section?${new URLSearchParams({ path, offset: String(offset), limit: String(limit) })}`),

  // 大纲
  draftLevel1: (id) => request(`/project/${id}/outline/draft-level1`, { method: 'POST', body: {} }),
  expandOutline: (id, chapters, totalWordBudget) =>
    request(`/project/${id}/outline/expand`, { method: 'POST', body: { chapters, total_word_budget: totalWordBudget } }),

  // 章节
  generateSection: (id, payload) => request(`/project/${id}/section/generate`, { method: 'POST', body: payload }),
  saveSection: (id, sectionId, content, status) =>
    request(`/project/${id}/section`, { method: 'PUT', body: { section_id: sectionId, content, status } }),
  polishSection: (id, payload) => request(`/project/${id}/section/polish`, { method: 'POST', body: payload }),
  generateBatch: (id, includeWritten = false) =>
    request(`/project/${id}/sections/generate-batch`, { method: 'POST', body: { include_written: includeWritten } }),
  // 章节检查：content 不传时检查已保存的正文；llmReview 加做模型评审（产生一次模型调用）
  checkSection: (id, sectionId, content, llmReview = false) =>
    request(`/project/${id}/section/${sectionId}/check`, { method: 'POST', body: { content, llm_review: llmReview } }),
  // 候选稿：AI 对已有正文的改动先成为候选稿，查看差异后采纳（409：正文或依据已变化，err.detail.reason）
  projectProposals: (id, open = true) => request(`/project/${id}/proposals?open=${open}`),
  sectionProposals: (id, sectionId) => request(`/project/${id}/section/${sectionId}/proposals`),
  getProposal: (id, sectionId, proposalId) => request(`/project/${id}/section/${sectionId}/proposals/${proposalId}`),
  applyProposal: (id, sectionId, proposalId) =>
    request(`/project/${id}/section/${sectionId}/proposals/${proposalId}/apply`, { method: 'POST' }),
  rejectProposal: (id, sectionId, proposalId) =>
    request(`/project/${id}/section/${sectionId}/proposals/${proposalId}/reject`, { method: 'POST' }),
  // 基于当前正文定向修订（后台任务，需配置模型），结果为候选稿
  reviseSection: (id, sectionId, { parentId = '', instruction = '' } = {}) =>
    request(`/project/${id}/section/${sectionId}/revise`, { method: 'POST', body: { parent_id: parentId, instruction } }),
  // 智能完善（后台任务，需配置模型）：起草或以当前正文为原稿 → 检查 → 定向修订，产出带检查报告的候选稿；
  // resume 从最后一版候选稿继续（轮数不重新计算）
  refineSection: (id, sectionId, { instruction = '', maxRounds = 2, applyIfBlank = false, resume = false } = {}) =>
    request(`/project/${id}/section/${sectionId}/refine`, {
      method: 'POST',
      body: { instruction, max_rounds: maxRounds, apply_if_blank: applyIfBlank, resume },
    }),
  listVersions: (id, sectionId) => request(`/project/${id}/section/${sectionId}/versions`),
  getVersion: (id, sectionId, versionId) => request(`/project/${id}/section/${sectionId}/versions/${versionId}`),
  restoreVersion: (id, sectionId, versionId) =>
    request(`/project/${id}/section/${sectionId}/versions/${versionId}/restore`, { method: 'POST' }),
  streamSection: (id, payload, onEvent, onError, onClose) =>
    streamSSE(`/project/${id}/section/generate/stream`, payload, onEvent, onError, onClose),

  // 偏离表
  extractDeviations: (id) => request(`/project/${id}/deviation/extract`, { method: 'POST', body: {} }),
  getDeviations: (id) => request(`/project/${id}/deviation`),
  saveDeviations: (id, items) => request(`/project/${id}/deviation`, { method: 'PUT', body: items }),
  generateDeviations: (id) => request(`/project/${id}/deviation/generate`, { method: 'POST' }),
  injectDeviations: (id, sectionId) =>
    request(`/project/${id}/deviation/inject`, { method: 'POST', body: { section_id: sectionId } }),

  // 证明材料：评分项与企业资料的关联（建议须用户确认后才经 linkEvidence 保存）
  evidence: (id) => request(`/project/${id}/evidence`),
  evidenceSuggestions: (id) => request(`/project/${id}/evidence/suggestions`),
  linkEvidence: (id, itemId, assetKeys) =>
    request(`/project/${id}/evidence/${encodeURIComponent(itemId)}`, { method: 'PUT', body: { asset_keys: assetKeys } }),

  // 合规质检
  complianceCheck: (id) => request(`/project/${id}/compliance/check`, { method: 'POST' }),
  qualityInspect: (id) => request(`/project/${id}/quality/inspect`, { method: 'POST' }),

  // 知识库
  kbUpload: (file, curate) => uploadFile('/knowledge/upload', file, { curate }),
  kbDocuments: () => request('/knowledge/documents'),
  kbDelete: (docId) => request(`/knowledge/documents/${docId}`, { method: 'DELETE' }),
  // 补齐/重建知识库中与当前嵌入模型不兼容的向量；已有重建任务在跑时返回该任务
  kbReindex: () => request('/knowledge/reindex', { method: 'POST' }),
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
    clearExamples: () => request('/assets/examples', { method: 'DELETE' }),
    // 证明附件（资质 / 人员 / 业绩）
    uploadAttachment: (type, id, file) => uploadFile(`/assets/${type}/${id}/attachments`, file),
    removeAttachment: (type, id, fileId) => request(`/assets/${type}/${id}/attachments/${fileId}`, { method: 'DELETE' }),
    attachmentUrl: (type, id, fileId) => `${BASE}/assets/${type}/${id}/attachments/${fileId}`,
    // 证明材料检查：传 projectId 时按该项目的投标截止时间与投标人全称核对
    materialCheck: (projectId) => request(`/assets/material-check${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`),
  },

  // 模板与导出
  templates: () => request('/templates/list'),
  createTemplate: (data) => request('/templates/create', { method: 'POST', body: data }),
  // diagrams：前端用 Mermaid 渲染好的架构图 [{code, image}]
  exportBid: (id, templateId, diagrams) =>
    downloadPost(`/project/${id}/export`, { template_id: templateId || 'gov_standard', diagrams }, '技术标书.docx'),
  // 导出前检查清单（只提示，不阻止导出）
  exportPreflight: (id) => request(`/project/${id}/export/preflight`),
  deviationExportUrl: (id, templateId) =>
    `${BASE}/project/${id}/deviation/export?template_id=${templateId || 'gov_standard'}`,

  // 任务
  pollTask,
  listTasks,
  latestTask,
  cancelTask: (taskId) => request(`/tasks/${taskId}/cancel`, { method: 'POST' }),
}

export default api
