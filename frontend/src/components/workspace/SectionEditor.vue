<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAiStore } from '@/stores/ai'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import EmptyState from '@/components/common/EmptyState.vue'

const props = defineProps({
  node: { type: Object, default: null },
  projectId: { type: String, required: true },
})
const emit = defineEmits(['node-updated', 'refs-updated'])

const ai = useAiStore()

const content = ref('')
const viewMode = ref('edit') // edit | preview
const generating = ref(false)
const polishing = ref(false)
const dirty = ref(false)
const savedStatus = ref('')
const customInstruction = ref('')
const abortFn = ref(null)
const lastRefs = ref([])
const retrievalMessage = ref('')
const generationMode = ref('')

// 自动保存（防抖）
let saveTimer = null
let stopWatch = null

watch(
  () => props.node,
  (n) => {
    if (!n) return
    content.value = n.content || ''
    customInstruction.value = n.requirements?.join('；') || ''
    lastRefs.value = n.last_refs || []
    dirty.value = false
  },
  { immediate: true }
)

watch(content, () => {
  dirty.value = true
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(autosave, 1500)
})

async function autosave() {
  if (!dirty.value || !props.node) return
  try {
    await api.saveSection(props.projectId, props.node.id, content.value, props.node.status || 'completed')
    dirty.value = false
    savedStatus.value = props.node.status
    emit('node-updated', { id: props.node.id, status: props.node.status })
  } catch (e) {
    ElMessage.error('自动保存失败：' + e.message)
  }
}

function toggleView() {
  if (viewMode.value === 'edit' && dirty.value) autosave()
  viewMode.value = viewMode.value === 'edit' ? 'preview' : 'edit'
}

async function generate() {
  if (!props.node) return
  if (dirty.value) await autosave()
  generating.value = true
  retrievalMessage.value = ''
  lastRefs.value = []
  generationMode.value = ''
  content.value = ''
  let buffer = ''

  abortFn.value = api.streamSection(
    props.projectId,
    {
      project_id: props.projectId,
      section_id: props.node.id,
      section_title: props.node.title,
      section_path: props.node.path || '',
      requirements: props.node.requirements || [],
      custom_instruction: customInstruction.value,
      pinned_refs: props.node.pinned_refs || [],
      excluded_refs: props.node.excluded_refs || [],
    },
    (event) => {
      if (event.token) {
        buffer += event.token
        content.value = buffer
      } else if (event.refs !== undefined) {
        lastRefs.value = event.refs
        retrievalMessage.value = event.retrieval_message || ''
        emit('refs-updated', event.refs)
      } else if (event.done !== undefined) {
        generationMode.value = event.mode || 'llm'
        ai.noteMode(event.mode)
        dirty.value = false // 后端已保存
        emit('node-updated', { id: props.node.id, status: event.status || 'completed' })
        if (event.error) ElMessage.error(event.error)
        else if (retrievalMessage.value) ElMessage.info(retrievalMessage.value)
        else ElMessage.success('章节撰写完成')
      } else if (event.error) {
        ElMessage.error('生成中断：' + event.error)
      }
    },
    (e) => {
      ElMessage.error('流式连接失败：' + e.message)
    }
  )
  generating.value = false
  abortFn.value = null
}

function stopGenerate() {
  if (abortFn.value) abortFn.value()
  generating.value = false
  ElMessage.info('已停止生成（保留已生成内容）')
}

async function polish() {
  if (!props.node || !content.value.trim()) {
    ElMessage.warning('章节尚无内容，请先撰写')
    return
  }
  polishing.value = true
  try {
    const res = await api.polishSection(props.projectId, {
      project_id: props.projectId,
      section_id: props.node.id,
      content: content.value,
      polish_mode: 'de_ai',
    })
    content.value = res.polished_content
    ai.noteMode(res.mode)
    dirty.value = false
    emit('node-updated', { id: props.node.id, status: 'reviewed' })
    ElMessage.success('降AI味润色完成' + (res.improvements?.length ? `：${res.improvements.slice(0, 2).join('；')}` : ''))
  } catch (e) {
    ElMessage.error('润色失败：' + e.message)
  } finally {
    polishing.value = false
  }
}

async function markReviewed() {
  await autosave()
  try {
    await api.saveSection(props.projectId, props.node.id, content.value, 'reviewed')
    emit('node-updated', { id: props.node.id, status: 'reviewed' })
    ElMessage.success('已标记校审')
  } catch (e) {
    ElMessage.error('保存失败：' + e.message)
  }
}

const wordCount = computed(() => content.value.replace(/\s/g, '').length)
const budgetHint = computed(() => {
  if (!props.node?.word_budget) return ''
  const diff = wordCount.value - props.node.word_budget
  if (diff > props.node.word_budget * 0.15) return `超预算 ${diff} 字`
  if (diff < -props.node.word_budget * 0.15) return `距目标还差 ${-diff} 字`
  return '字数达标'
})

onBeforeUnmount(() => {
  if (saveTimer) clearTimeout(saveTimer)
  if (abortFn.value) abortFn.value()
})
</script>

<template>
  <div v-if="node" class="h-full flex flex-col">
    <!-- 工具栏 -->
    <div class="flex items-center justify-between px-3 py-2 border-b border-slate-200 bg-white shrink-0 gap-2 flex-wrap">
      <div class="flex items-center gap-2 min-w-0">
        <h2 class="font-bold text-slate-800 truncate max-w-[40vw]">{{ node.title }}</h2>
        <span v-if="node.word_budget" class="text-[11px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-600 shrink-0">
          {{ wordCount }} / {{ node.word_budget }} 字
        </span>
      </div>
      <div class="flex items-center gap-1.5 shrink-0">
        <span v-if="dirty" class="text-[11px] text-amber-600">● 未保存</span>
        <span v-else-if="content" class="text-[11px] text-emerald-600">已保存</span>
        <el-button size="small" text @click="toggleView">
          {{ viewMode === 'edit' ? '预览' : '编辑' }}
        </el-button>
        <el-button v-if="viewMode === 'edit'" size="small" text type="primary" :loading="polishing" @click="polish">
          降AI味润色
        </el-button>
        <el-button size="small" text type="success" :disabled="!content" @click="markReviewed">标记校审</el-button>
      </div>
    </div>

    <!-- 生成控制行 -->
    <div class="flex items-center gap-2 px-3 py-2 bg-slate-50 border-b border-slate-200 shrink-0 flex-wrap">
      <el-button
        v-if="!generating"
        type="primary"
        size="small"
        @click="generate"
      >
        <el-icon class="mr-1"><MagicStick /></el-icon>AI 智能流式撰写
      </el-button>
      <el-button v-else type="danger" size="small" @click="stopGenerate">
        <el-icon class="mr-1"><VideoPause /></el-icon>停止生成
      </el-button>
      <span v-if="generating" class="text-xs text-blue-600 animate-pulse">正在生成…</span>
      <span v-else-if="retrievalMessage" class="text-xs text-slate-500 truncate flex-1 min-w-0" :title="retrievalMessage">
        {{ retrievalMessage }}
      </span>
      <el-tag v-if="generationMode === 'mock' || (ai.isMockMode && content)" size="small" type="warning" effect="plain">
        演示内容
      </el-tag>
    </div>

    <!-- 编辑 / 预览 -->
    <div class="flex-1 min-h-0 bg-white">
      <div v-if="viewMode === 'edit'" class="h-full flex flex-col">
        <textarea
          v-model="content"
          class="flex-1 w-full resize-none outline-none p-4 text-[13.5px] leading-7 text-slate-800"
          placeholder="选择左侧章节后，点击【AI 智能流式撰写】基于知识库与全局事实生成正文。也可直接在此编辑，内容自动保存。"
        />
      </div>
      <MarkdownPreview v-else :content="content" mode="preview" />
    </div>
  </div>

  <div v-else class="h-full flex items-center justify-center text-slate-400 text-sm">
    <EmptyState icon="Pointer" title="从左侧选择章节开始撰写" />
  </div>
</template>
