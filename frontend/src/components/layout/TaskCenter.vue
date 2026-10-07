<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useTaskStore } from '@/stores/tasks'
import { isActiveTask, taskRoute, taskStatusMeta, taskTypeLabel } from '@/utils/tasks'

/**
 * 任务中心：最近的后台任务（含服务重启前的历史与"已中断"的任务），点击跳转到对应页面。
 */
defineProps({
  collapsed: { type: Boolean, default: false },
})

const router = useRouter()
const store = useTaskStore()
const open = ref(false)

// 失败任务显示错误原因，其余显示最近一条进度描述
function detail(task) {
  if (task.status === 'failed') return (task.error || '').split('\n')[0]
  return task.message !== task.title ? task.message : ''
}

const DOT = { primary: 'bg-accent', success: 'bg-ok', danger: 'bg-bad', warning: 'bg-warn', info: 'bg-ink-3' }
const CHIP = { primary: 'chip-accent', success: 'chip-ok', danger: 'chip-bad', warning: 'chip-warn', info: 'chip-mute' }

function go(task) {
  const path = taskRoute(task)
  if (!path) return
  open.value = false
  router.push(path)
}

watch(open, (v) => store.setPanelOpen(v))
onMounted(() => store.start())
onUnmounted(() => {
  if (open.value) store.setPanelOpen(false)
  store.stop()
})
</script>

<template>
  <el-popover v-model:visible="open" trigger="click" placement="right-end" :width="360" :offset="14" popper-class="!p-0 !rounded-xl overflow-hidden">
    <template #reference>
      <button
        class="side-row group w-full"
        :class="[{ 'is-active': open }, collapsed ? 'md:justify-center md:px-0' : '']"
        title="后台任务"
      >
        <span class="relative inline-flex">
          <el-icon :size="17" :class="{ 'animate-spin': store.activeCount }">
            <component :is="store.activeCount ? 'Loading' : 'Tickets'" />
          </el-icon>
          <span
            v-if="store.activeCount && collapsed"
            class="hidden md:block absolute -top-1 -right-1.5 w-2 h-2 rounded-full bg-accent ring-2 ring-sunken"
          />
        </span>
        <span class="flex-1 text-left" :class="collapsed ? 'md:hidden' : ''">后台任务</span>
        <span
          v-if="store.activeCount"
          class="min-w-[1.25rem] h-5 px-1.5 rounded-full bg-accent text-white text-2xs leading-5 text-center num"
          :class="collapsed ? 'md:hidden' : ''"
        >{{ store.activeCount }}</span>
      </button>
    </template>

    <div class="px-4 pt-3.5 pb-2.5 border-b border-line">
      <p class="text-sm font-semibold text-ink">后台任务</p>
      <p class="text-2xs text-ink-3 mt-0.5">最近 20 条 · 服务重启时未完成的任务显示"已中断"</p>
    </div>
    <div class="max-h-[26rem] overflow-auto py-1">
      <p v-if="store.error" class="px-4 py-4 text-xs text-bad">任务列表加载失败：{{ store.error }}</p>
      <p v-else-if="!store.tasks.length" class="px-4 py-10 text-center text-xs text-ink-3">暂无后台任务</p>
      <button
        v-for="t in store.tasks"
        :key="t.id"
        class="w-full text-left px-4 py-2.5 flex gap-3 transition-colors"
        :class="taskRoute(t) ? 'hover:bg-raised cursor-pointer' : 'cursor-default'"
        @click="go(t)"
      >
        <span class="dot mt-1.5" :class="[DOT[taskStatusMeta(t).type], { 'animate-pulse': isActiveTask(t) }]" />
        <span class="flex-1 min-w-0">
          <span class="flex items-center justify-between gap-2">
            <span class="text-xs font-medium text-ink truncate">{{ t.title || taskTypeLabel(t) }}</span>
            <span class="chip shrink-0" :class="CHIP[taskStatusMeta(t).type]">
              {{ taskStatusMeta(t).label }}<template v-if="isActiveTask(t)"> <span class="num">{{ t.progress }}%</span></template>
            </span>
          </span>
          <span
            v-if="detail(t)"
            class="block text-2xs mt-0.5 truncate"
            :class="t.status === 'interrupted' ? 'text-warn' : t.status === 'failed' ? 'text-bad' : 'text-ink-2'"
            :title="detail(t)"
          >{{ detail(t) }}</span>
          <span class="block text-2xs text-ink-3 mt-0.5">{{ taskTypeLabel(t) }} · {{ t.updated_at || t.created_at }}</span>
        </span>
      </button>
    </div>
  </el-popover>
</template>
