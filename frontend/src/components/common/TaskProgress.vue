<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import api from '@/api/client'

/**
 * 后台任务进度条：传入 taskId 自动轮询，done/failed 事件回传最终任务对象。
 */
const props = defineProps({
  taskId: { type: String, required: true },
  title: { type: String, default: '任务执行中' },
})
const emit = defineEmits(['done', 'failed'])

const task = ref(null)
let cancelled = false

async function poll() {
  try {
    task.value = await api.pollTask(props.taskId, (t) => {
      if (cancelled) return
      task.value = t
    })
    if (task.value.status === 'failed') emit('failed', task.value)
    else emit('done', task.value)
  } catch (e) {
    emit('failed', { error: e.message })
  }
}

onMounted(poll)
onUnmounted(() => { cancelled = true })
</script>

<template>
  <div class="py-2">
    <div class="flex items-center justify-between mb-1.5 gap-2">
      <span class="text-sm font-medium text-slate-700 truncate">{{ title }}</span>
      <span class="text-xs text-slate-500 shrink-0">
        <template v-if="task && task.status === 'failed'">
          <span class="text-red-600 font-medium">失败</span>
        </template>
        <template v-else>{{ task ? task.progress : 0 }}%</template>
      </span>
    </div>
    <el-progress
      :percentage="task ? task.progress : 0"
      :status="task && task.status === 'failed' ? 'exception' : task && task.status === 'completed' ? 'success' : undefined"
      :stroke-width="10"
    />
    <p class="mt-1.5 text-xs text-slate-500 leading-relaxed">
      {{ task ? task.message : '提交中…' }}
    </p>
    <p v-if="task && task.status === 'failed'" class="mt-1 text-xs text-red-600 leading-relaxed max-h-24 overflow-auto whitespace-pre-wrap">
      {{ (task.error || '').split('\n')[0] }}
    </p>
  </div>
</template>
