<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import api from '@/api/client'
import { useTaskStore } from '@/stores/tasks'

/**
 * 后台任务进度条：传入 taskId 自动轮询。
 * 成功或取消回传 done；失败、服务重启导致的中断回传 failed（均携带最终任务对象）。
 */
const props = defineProps({
  taskId: { type: String, required: true },
  title: { type: String, default: '任务执行中' },
  cancellable: { type: Boolean, default: false },
})
const emit = defineEmits(['done', 'failed'])

const task = ref(null)
const controller = new AbortController()
const taskStore = useTaskStore()

// 任务开始/结束时同步任务中心角标
function syncTaskCenter() {
  if (taskStore.watchers) taskStore.refresh()
}

async function poll() {
  syncTaskCenter()
  try {
    const final = await api.pollTask(props.taskId, (t) => { task.value = t }, { signal: controller.signal })
    task.value = final
    syncTaskCenter()
    if (['failed', 'interrupted'].includes(final.status)) emit('failed', final)
    else emit('done', final)
  } catch (e) {
    if (e.name !== 'AbortError') emit('failed', { status: 'failed', error: e.message })
  }
}

const cancelling = ref(false)
async function cancelTask() {
  cancelling.value = true
  try {
    await api.cancelTask(props.taskId)
  } catch { /* 任务可能已结束 */ }
}

const progressStatus = computed(() => {
  const s = task.value?.status
  if (s === 'failed') return 'exception'
  if (s === 'interrupted') return 'warning'
  if (s === 'completed') return 'success'
  return undefined
})

const hint = computed(() => {
  if (!task.value) return '提交中…'
  if (task.value.reconnecting) return '与服务的连接中断，正在等待服务恢复…'
  return task.value.message
})

onMounted(poll)
onUnmounted(() => controller.abort())
</script>

<template>
  <div class="py-1">
    <div class="flex items-center justify-between mb-2 gap-3">
      <span class="flex items-center gap-2 min-w-0 text-sm font-medium text-ink">
        <span
          class="dot"
          :class="task?.status === 'failed' ? 'bg-bad' : task?.status === 'interrupted' || task?.reconnecting ? 'bg-warn' : task?.status === 'completed' ? 'bg-ok' : 'bg-accent animate-pulse'"
        />
        <span class="truncate">{{ title }}</span>
      </span>
      <span class="text-xs text-ink-3 shrink-0 flex items-center gap-2 num">
        <el-button
          v-if="cancellable && task?.status === 'running' && !task.reconnecting"
          size="small"
          text
          type="danger"
          :loading="cancelling"
          @click="cancelTask"
        >{{ cancelling ? '正在停止…' : '取消' }}</el-button>
        <span v-if="task?.status === 'failed'" class="text-bad font-semibold">失败</span>
        <span v-else-if="task?.status === 'interrupted'" class="text-warn font-semibold">已中断</span>
        <template v-else>{{ task ? task.progress : 0 }}%</template>
      </span>
    </div>
    <div class="meter !h-2">
      <span
        :style="{ width: `${task ? task.progress : 0}%` }"
        :class="{
          'bg-bad': progressStatus === 'exception',
          'bg-warn': progressStatus === 'warning',
          'bg-ok': progressStatus === 'success',
          'bg-accent': !progressStatus,
        }"
      />
    </div>
    <p class="mt-2 text-xs leading-relaxed" :class="task?.reconnecting || task?.status === 'interrupted' ? 'text-warn' : 'text-ink-3'">
      {{ hint }}
    </p>
    <p v-if="task?.status === 'failed'" class="mt-1 text-xs text-bad leading-relaxed max-h-24 overflow-auto whitespace-pre-wrap">
      {{ (task.error || '').split('\n')[0] }}
    </p>
  </div>
</template>
