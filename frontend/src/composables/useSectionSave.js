import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useProjectStore } from '@/stores/project'

const AUTOSAVE_DELAY = 1500

/**
 * 章节编辑器的正文保存状态：防抖自动保存、串行保存、按修订号丢弃过期响应。
 *
 * - content 为编辑器正文；savedContent / savedRevision 是最近确认落库的正文与修订号，dirty 由二者比较得出
 *   （避免异步 watcher 与标志位赛跑）
 * - 保存串行执行：较早请求的响应不会把较新的草稿标为已保存；保存失败保留草稿，不自动重试
 * - 服务端响应带修订号，低于编辑器或 store 已知修订号的响应一律丢弃（迟到的旧 409 / 旧保存结果）
 * - paused 为真（流式生成中）时不自动保存，正文由服务端在生成完成时写入
 *
 * node / projectId 为 getter，paused 为 ref。
 */
export function useSectionSave({ node, projectId, paused }) {
  const projectStore = useProjectStore()
  const content = ref('')
  const savedContent = ref('')
  const savedRevision = ref(0)
  const dirty = computed(() => content.value !== savedContent.value)

  let saveTimer = null
  let savePromise = null
  // 正文每次变化计数：改动后又改回原文也算任务期间发生过编辑
  let editCount = 0

  function clearSaveTimer() {
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = null
  }

  watch(content, () => { editCount += 1 }, { flush: 'sync' })

  // 在途请求完成后 savedContent 也会变化，可能使刚刚还原的正文重新变脏
  watch([content, savedContent], () => {
    clearSaveTimer()
    if (paused.value || !dirty.value) return
    saveTimer = setTimeout(autosave, AUTOSAVE_DELAY)
  })

  /**
   * 按 store 中的章节载入正文，返回是否载入。项目刷新只同步已保存的同章正文：
   * 防抖/请求中的草稿、流式输出、比已知修订号更旧的数据都不能当作服务器正文覆盖。
   */
  function syncNode(n, previous) {
    if (!n) return false
    if (n.id === previous?.id && (dirty.value || paused.value || (n.revision || 0) < savedRevision.value)) return false
    content.value = n.content || ''
    savedContent.value = content.value
    savedRevision.value = n.revision || 0
    return true
  }

  function isCurrentRevision(nodeId, revision) {
    const knownRevision = Math.max(
      node()?.id === nodeId ? savedRevision.value : 0,
      projectStore.findNode(nodeId)?.revision || 0,
    )
    return revision === undefined || revision >= knownRevision
  }

  // 已落库的正文同步回 store：切换章节再切回时编辑器不会加载旧内容
  function markSaved(nodeId, text, status, revision) {
    if (!isCurrentRevision(nodeId, revision)) return
    if (node()?.id === nodeId) {
      savedContent.value = text
      if (revision !== undefined) savedRevision.value = revision
    }
    projectStore.setSectionContent(nodeId, text, status, revision)
  }

  /** 保存当前草稿；返回保存后是否已无未保存修改（失败为 false） */
  async function autosave() {
    clearSaveTimer()
    if (savePromise) {
      if (!await savePromise) return false
      return autosave()
    }
    const current = node()
    if (!current || paused.value || !dirty.value) return !dirty.value
    const nodeId = current.id
    const text = content.value
    // 手写正文的待撰写章节视为已完成，计入进度
    const status = current.status === 'pending' && text.trim() ? 'completed' : (current.status || 'completed')
    savePromise = (async () => {
      try {
        const res = await api.saveSection(projectId(), nodeId, text, status)
        markSaved(nodeId, text, status, res.revision)
        return true
      } catch (e) {
        ElMessage.error('自动保存失败：' + e.message)
        return false
      }
    })()
    try { return await savePromise } finally { savePromise = null }
  }

  /** 保存到没有未保存修改为止（保存期间又有输入会接着保存）；保存失败返回 false */
  async function flushSave() {
    if (paused.value) return true // 流式结果由服务端写回；刷新也不会替换它
    do {
      if (!await autosave()) return false
    } while (dirty.value)
    return true
  }

  /** 离开章节：改回原文时可能暂时不脏，仍需等待在途保存，再比较并保存最后的编辑 */
  function saveOnLeave() {
    if (dirty.value || savePromise) autosave()
    clearSaveTimer()
  }

  return {
    content, savedRevision, dirty,
    syncNode, markSaved, isCurrentRevision, autosave, flushSave, saveOnLeave, clearSaveTimer,
    editMark: () => editCount,
    editedSince: (mark) => editCount !== mark,
  }
}
