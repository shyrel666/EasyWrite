<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { useAiStore } from '@/stores/ai'
import AppShell from '@/components/layout/AppShell.vue'
import TaskProgress from '@/components/common/TaskProgress.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import PageHeader from '@/components/common/PageHeader.vue'

const ai = useAiStore()

const stats = ref({ total_documents: 0, total_chunks: 0, documents: [] })
const uploading = ref(false)
const uploadTaskId = ref('')
const reindexTaskId = ref('')
const reindexStarting = ref(false)
const fileInput = ref(null)
const searchQuery = ref('')
const searchResults = ref(null)
const searching = ref(false)
const searchMessage = ref('')
const selectedDoc = ref(null)
const itemsOpen = ref(false)
const items = ref([])
const dragOver = ref(false)

async function load() {
  stats.value = await api.kbStats()
}

async function rebuildVectors() {
  if (reindexStarting.value || reindexTaskId.value) return
  reindexStarting.value = true
  try {
    reindexTaskId.value = (await api.kbReindex()).task_id
  } catch (e) {
    ElMessage.error('向量重建启动失败：' + e.message)
  } finally {
    reindexStarting.value = false
  }
}

async function onReindexDone(task) {
  reindexTaskId.value = ''
  if (task.status === 'completed') ElMessage.success('向量重建完成')
  else ElMessage.error('向量重建未完成：' + (task.error || task.message || '').split('\n')[0])
  await load()
}

function pickFile() {
  fileInput.value.click()
}

function onDrop(event) {
  dragOver.value = false
  const files = event.dataTransfer?.files
  if (files?.length) handleUpload({ target: { files } })
}

async function handleUpload(event) {
  const file = event.target.files[0]
  if (!file) return
  if (!/\.(docx|pdf)$/i.test(file.name)) {
    ElMessage.warning('仅支持 .docx / .pdf 格式文档')
    return
  }
  uploading.value = true
  try {
    const res = await api.kbUpload(file, true)
    uploadTaskId.value = res.task_id
  } catch (e) {
    uploading.value = false
    ElMessage.error('上传失败：' + e.message)
  }
}

function onUploadDone(task) {
  uploading.value = false
  uploadTaskId.value = ''
  if (task.status !== 'completed') {
    ElMessage.error('入库失败：' + (task.error || '').split('\n')[0])
    load()
    return
  }
  ElMessage.success(`入库完成：${task.result.doc_name}（${stats.value.total_chunks} 个知识块）`)
  ai.refresh()
  load()
}

async function removeDoc(doc) {
  try {
    await ElMessageBox.confirm(`删除文档「${doc.doc_name}」及其全部索引块？`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.kbDelete(doc.id)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    ElMessage.error('删除失败：' + e.message)
  }
}

async function runSearch() {
  if (!searchQuery.value.trim()) {
    ElMessage.warning('请输入检索内容')
    return
  }
  searching.value = true
  try {
    const res = await api.kbSearch({ query: searchQuery.value, top_k: 5, rerank: ai.llmConfigured })
    searchResults.value = res.refs
    searchMessage.value = res.message
    if (!res.refs.length) ElMessage.info(res.message || '无匹配结果')
  } catch (e) {
    ElMessage.error('检索失败：' + e.message)
  } finally {
    searching.value = false
  }
}

async function showItems(doc) {
  selectedDoc.value = doc
  itemsOpen.value = true
  const res = await api.kbItems(doc.id)
  items.value = res.items || []
}

async function curate(doc) {
  try {
    const res = await api.kbCurate(doc.id)
    ElMessage.success('条目策展任务已启动')
    const task = await api.pollTask(res.task_id)
    if (task.status !== 'completed') {
      ElMessage.error('策展失败：' + (task.error || '').split('\n')[0])
      return
    }
    ElMessage.success('条目策展完成')
    showItems(doc)
  } catch (e) {
    ElMessage.error('策展失败：' + e.message)
  }
}

function statusMeta(doc) {
  if (doc.status === 'ready') return { label: '已入库', chip: 'chip-ok' }
  if (doc.status === 'ingesting') return { label: '入库中', chip: 'chip-warn' }
  return { label: '失败', chip: 'chip-bad' }
}

onMounted(async () => {
  await load()
  // 接管刷新前仍在进行的入库任务
  try {
    const { tasks } = await api.listTasks({ type: 'kb_ingest', active: true, limit: 1 })
    if (tasks.length) {
      uploadTaskId.value = tasks[0].id
      uploading.value = true
    }
  } catch { /* 忽略 */ }
  try {
    const { tasks } = await api.listTasks({ type: 'kb_reindex', active: true, limit: 1 })
    if (tasks.length) reindexTaskId.value = tasks[0].id
  } catch { /* 忽略 */ }
})
</script>

<template>
  <AppShell active="knowledge">
    <div class="max-w-6xl mx-auto px-4 sm:px-8 py-6 sm:py-10 space-y-6">
      <PageHeader
        eyebrow="知识库"
        title="历史标书知识库"
        description="结构感知分块 + BM25 / 向量混合索引 + LLM 重排。撰写时按章节主题检索，命中附分数与来源，可在编纂台锁定或排除。"
      >
        <template #actions>
          <span class="chip" :class="stats.dense_enabled ? 'chip-ok' : 'chip-mute'">
            <span class="dot" :class="stats.dense_enabled ? 'bg-ok' : 'bg-ink-3'" />
            {{ stats.dense_enabled ? `向量检索：${stats.embedding_model}` : '向量检索未配置 · BM25 + 重排兜底' }}
          </span>
          <el-button type="primary" @click="pickFile">
            <el-icon class="mr-1.5"><Upload /></el-icon>上传历史标书
          </el-button>
          <input ref="fileInput" type="file" accept=".docx,.pdf" class="hidden" @change="handleUpload" />
        </template>
      </PageHeader>
      <div v-if="stats.embedding_warning" class="note note-warn items-center">
        <span class="flex-1">{{ stats.embedding_warning }}</span>
        <el-button size="small" :loading="reindexStarting" :disabled="!!reindexTaskId" @click="rebuildVectors">重建全部向量</el-button>
      </div>
      <TaskProgress v-if="reindexTaskId" :task-id="reindexTaskId" title="正在重建向量" @done="onReindexDone" @failed="onReindexDone" />

      <!-- 文档 -->
      <section
        class="card overflow-hidden rise transition"
        :class="dragOver ? 'ring-2 ring-accent/40 border-accent/50' : ''"
        @dragover.prevent="dragOver = true"
        @dragleave.prevent="dragOver = false"
        @drop.prevent="onDrop"
      >
        <div class="px-5 sm:px-6 py-4 flex items-center justify-between gap-3 border-b border-line">
          <h2 class="text-sm font-semibold text-ink">
            文档 <span class="text-ink-3 font-normal num ml-1">{{ stats.total_documents || 0 }} 份 · {{ stats.total_chunks || 0 }} 个知识块</span>
          </h2>
          <span class="hint hidden sm:inline">可将 .docx / .pdf 拖到此处上传</span>
        </div>

        <div v-if="uploading && uploadTaskId" class="px-5 sm:px-6 py-4 border-b border-line bg-raised">
          <TaskProgress
            :task-id="uploadTaskId"
            title="正在入库：解析 → 语义分块 → 上下文增强 → 双通道索引 → 条目策展"
            @done="onUploadDone"
            @failed="onUploadDone"
          />
        </div>

        <EmptyState
          v-if="!stats.documents?.length"
          compact
          icon="Collection"
          title="知识库还是空的"
          description="上传过往中标的技术标书，撰写时会自动检索相似章节作为参考。"
        >
          <el-button @click="pickFile">选择文件</el-button>
        </EmptyState>

        <ul v-else class="divide-y divide-line">
          <li
            v-for="doc in stats.documents"
            :key="doc.id"
            class="px-5 sm:px-6 py-3.5 flex items-center gap-4 hover:bg-raised/60 transition-colors"
          >
            <span class="w-9 h-11 shrink-0 rounded-[4px] border border-line bg-surface shadow-sm flex flex-col gap-[3px] p-1.5 pt-2">
              <span class="h-[3px] w-2/3 rounded bg-accent/60" />
              <span v-for="n in 3" :key="n" class="h-[2px] rounded bg-line-strong" />
            </span>
            <div class="min-w-0 flex-1">
              <p class="text-sm font-medium text-ink truncate" :title="doc.doc_name">{{ doc.doc_name }}</p>
              <p class="text-2xs text-ink-3 mt-1 num">{{ doc.chunk_count }} 块 · {{ doc.item_count }} 条目 · {{ doc.created_at }}</p>
              <p v-if="doc.error_msg" class="text-2xs text-bad mt-0.5 line-clamp-1" :title="doc.error_msg">{{ doc.error_msg }}</p>
            </div>
            <span class="chip shrink-0" :class="statusMeta(doc).chip">{{ statusMeta(doc).label }}</span>
            <div class="flex items-center shrink-0">
              <el-button size="small" text @click="showItems(doc)">条目</el-button>
              <el-tooltip :content="ai.llmConfigured ? '由大模型提炼可复用的知识条目' : '策展需要先配置大模型'" placement="top">
                <span><el-button size="small" text type="primary" :disabled="!ai.llmConfigured" @click="curate(doc)">策展</el-button></span>
              </el-tooltip>
              <button class="icon-btn !w-7 !h-7 hover:!text-bad" title="删除" @click="removeDoc(doc)"><el-icon><Delete /></el-icon></button>
            </div>
          </li>
        </ul>
      </section>

      <!-- 检索测试台 -->
      <section class="card p-5 sm:p-6 rise" style="animation-delay: 60ms">
        <h2 class="text-sm font-semibold text-ink">检索测试台</h2>
        <p class="hint mt-1 mb-4">输入章节主题或技术需求，查看混合检索命中的片段与分数{{ ai.llmConfigured ? '（含 LLM 重排）' : '' }}。</p>
        <div class="flex gap-2">
          <el-input
            v-model="searchQuery"
            size="large"
            placeholder="如：数据库容灾与高可用方案"
            @keyup.enter="runSearch"
          >
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
          <el-button type="primary" size="large" :loading="searching" @click="runSearch">检索</el-button>
        </div>
        <p v-if="searchMessage" class="text-xs text-ink-2 mt-3">{{ searchMessage }}</p>

        <ol v-if="searchResults?.length" class="mt-4 space-y-2.5">
          <li v-for="(r, i) in searchResults" :key="r.chunk_id" class="rounded-lg border border-line p-4 flex gap-4">
            <span class="font-kai text-xl text-ink-3 leading-none w-5 shrink-0 num">{{ i + 1 }}</span>
            <div class="min-w-0 flex-1">
              <div class="flex items-start justify-between gap-3 flex-wrap">
                <p class="text-sm font-medium text-ink">{{ r.section_title }}</p>
                <div class="flex items-center gap-1.5 shrink-0">
                  <span v-if="r.rerank_score != null" class="chip" :class="r.rerank_score >= 8 ? 'chip-ok' : r.rerank_score >= 6 ? 'chip-warn' : 'chip-mute'">重排 {{ r.rerank_score }}</span>
                  <span class="chip chip-mute num">BM25 {{ r.bm25_score.toFixed(3) }}</span>
                  <span v-if="r.dense_score" class="chip chip-mute num">向量 {{ r.dense_score.toFixed(3) }}</span>
                </div>
              </div>
              <p class="text-2xs text-ink-3 mt-1 truncate">{{ r.doc_name }} / {{ r.breadcrumb }}</p>
              <p class="text-xs text-ink-2 leading-relaxed line-clamp-3 mt-2">{{ r.content }}</p>
            </div>
          </li>
        </ol>
      </section>

      <!-- 条目 -->
      <el-drawer v-model="itemsOpen" direction="rtl" size="min(94%, 540px)" :title="`知识条目 · ${selectedDoc?.doc_name || ''}`" @closed="selectedDoc = null">
        <EmptyState v-if="!items.length" icon="Collection" title="暂无知识条目" description="点击文档上的「策展」，由大模型提炼可复用知识条目（标题 + 用途摘要 + 源块溯源）。" />
        <div v-else class="space-y-2">
          <article v-for="it in items" :key="it.id" class="rounded-lg border border-line p-3.5">
            <p class="text-sm font-medium text-ink">{{ it.title }}</p>
            <p class="text-xs text-ink-2 mt-1.5 leading-relaxed">{{ it.summary }}</p>
            <p class="text-2xs text-ink-3 mt-2">源块 {{ it.chunk_ids.length }} 个 · 全程溯源</p>
          </article>
        </div>
      </el-drawer>
    </div>
  </AppShell>
</template>
