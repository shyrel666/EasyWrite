<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { useAiStore } from '@/stores/ai'
import AppShell from '@/components/layout/AppShell.vue'
import TaskProgress from '@/components/common/TaskProgress.vue'
import EmptyState from '@/components/common/EmptyState.vue'

const ai = useAiStore()

const stats = ref({ total_documents: 0, total_chunks: 0, documents: [] })
const uploading = ref(false)
const uploadTaskId = ref('')
const fileInput = ref(null)
const searchQuery = ref('')
const searchResults = ref(null)
const searching = ref(false)
const searchMessage = ref('')
const selectedDoc = ref(null)
const items = ref([])

async function load() {
  stats.value = await api.kbStats()
}

function pickFile() {
  fileInput.value.click()
}

async function handleUpload(event) {
  const file = event.target.files[0]
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.docx')) {
    ElMessage.warning('仅支持 .docx 格式文档')
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
  const res = await api.kbItems(doc.id)
  items.value = res.items || []
}

async function curate(doc) {
  try {
    const res = await api.kbCurate(doc.id)
    ElMessage.success('条目策展任务已启动')
    // 简单等待后刷新
    await api.pollTask(res.task_id)
    ElMessage.success('条目策展完成')
    showItems(doc)
  } catch (e) {
    ElMessage.error('策展失败：' + e.message)
  }
}

function statusMeta(doc) {
  if (doc.status === 'ready') return { label: '已入库', type: 'success' }
  if (doc.status === 'ingesting') return { label: '入库中', type: 'warning' }
  return { label: '失败', type: 'danger' }
}

onMounted(load)
</script>

<template>
  <AppShell active="knowledge">
    <div class="max-w-6xl mx-auto px-3 sm:px-6 py-5 space-y-5">
      <!-- 上传区 -->
      <div class="bg-white rounded-xl border border-slate-200 p-5">
        <div class="flex items-center justify-between flex-wrap gap-3">
          <div>
            <h2 class="font-bold text-slate-800">历史标书知识库</h2>
            <p class="text-xs text-slate-500 mt-0.5">
              结构感知分块 + BM25/向量混合索引 + LLM 重排。检索命中附分数与来源溯源，可锁定/排除。
            </p>
          </div>
          <div class="flex items-center gap-2">
            <el-tag size="small" :type="stats.dense_enabled ? 'success' : 'info'" effect="light">
              嵌入：{{ stats.dense_enabled ? stats.embedding_model : '未配置（BM25+重排兜底）' }}
            </el-tag>
            <el-button type="primary" @click="pickFile">
              <el-icon class="mr-1"><Upload /></el-icon>上传历史标书 (.docx)
            </el-button>
            <input ref="fileInput" type="file" accept=".docx" class="hidden" @change="handleUpload" />
          </div>
        </div>

        <div v-if="uploading && uploadTaskId" class="mt-4">
          <TaskProgress
            :task-id="uploadTaskId"
            title="正在入库：解析 → 语义分块 → 上下文增强 → 双通道索引 → 条目策展"
            @done="onUploadDone"
            @failed="onUploadDone"
          />
        </div>

        <!-- 文档列表 -->
        <div v-if="stats.documents?.length" class="mt-4 space-y-2">
          <div
            v-for="doc in stats.documents"
            :key="doc.id"
            class="flex items-center justify-between gap-3 border border-slate-200 rounded-lg px-3 py-2.5 hover:border-blue-300 transition"
          >
            <div class="min-w-0">
              <p class="text-sm font-medium text-slate-700 truncate">{{ doc.doc_name }}</p>
              <p class="text-[11px] text-slate-400">{{ doc.chunk_count }} 块 · {{ doc.item_count }} 条目 · {{ doc.created_at }}</p>
              <p v-if="doc.error_msg" class="text-[11px] text-red-500 line-clamp-1">{{ doc.error_msg }}</p>
            </div>
            <div class="flex items-center gap-1.5 shrink-0">
              <el-tag size="small" :type="statusMeta(doc).type" effect="light">{{ statusMeta(doc).label }}</el-tag>
              <el-button size="small" text @click="showItems(doc)">条目</el-button>
              <el-button size="small" text type="primary" :disabled="!ai.llmConfigured" @click="curate(doc)">策展</el-button>
              <el-button size="small" text type="danger" @click="removeDoc(doc)">删除</el-button>
            </div>
          </div>
        </div>
      </div>

      <!-- 检索测试台 -->
      <div class="bg-white rounded-xl border border-slate-200 p-5">
        <h2 class="font-bold text-slate-800 mb-3">混合检索测试台</h2>
        <div class="flex gap-2">
          <el-input
            v-model="searchQuery"
            placeholder="输入章节主题或技术需求，如：数据库容灾与高可用方案"
            @keyup.enter="runSearch"
          />
          <el-button type="primary" :loading="searching" @click="runSearch">检索</el-button>
        </div>
        <p v-if="searchMessage" class="text-xs text-slate-500 mt-2">{{ searchMessage }}</p>
        <div v-if="searchResults?.length" class="mt-3 space-y-2">
          <div
            v-for="r in searchResults"
            :key="r.chunk_id"
            class="border border-slate-200 rounded-lg p-3 text-sm"
          >
            <div class="flex items-center justify-between gap-2 mb-1">
              <span class="font-medium text-slate-700">{{ r.section_title }}</span>
              <div class="flex items-center gap-2 shrink-0">
                <span v-if="r.rerank_score != null" class="text-xs px-1.5 py-0.5 rounded" :class="r.rerank_score >= 8 ? 'bg-emerald-100 text-emerald-700' : r.rerank_score >= 6 ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-500'">
                  重排 {{ r.rerank_score }}
                </span>
                <span class="text-[11px] text-slate-400">BM25 {{ r.bm25_score.toFixed(3) }}</span>
                <span v-if="r.dense_score" class="text-[11px] text-slate-400">向量 {{ r.dense_score.toFixed(3) }}</span>
              </div>
            </div>
            <p class="text-xs text-slate-400 mb-1">{{ r.doc_name }} / {{ r.breadcrumb }}</p>
            <p class="text-slate-600 leading-relaxed line-clamp-3">{{ r.content }}</p>
          </div>
        </div>
      </div>

      <!-- 条目弹窗 -->
      <el-drawer v-model="selectedDoc" direction="rtl" size="min(92%, 520px)" :title="`知识条目 · ${selectedDoc?.doc_name || ''}`">
        <EmptyState v-if="!items.length" icon="Collection" title="暂无知识条目" description="点击文档上的「策展」按钮，由 LLM 提炼可复用知识条目（标题+用途摘要+源块溯源）" />
        <div v-else class="space-y-2 pr-2">
          <div v-for="it in items" :key="it.id" class="border border-slate-200 rounded-lg p-3 text-sm">
            <p class="font-medium text-slate-800">{{ it.title }}</p>
            <p class="text-xs text-slate-500 mt-1">{{ it.summary }}</p>
            <p class="text-[11px] text-slate-400 mt-1.5">源块：{{ it.chunk_ids.length }} 个（全程溯源）</p>
          </div>
        </div>
      </el-drawer>
    </div>
  </AppShell>
</template>
