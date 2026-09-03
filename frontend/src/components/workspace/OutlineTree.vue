<script setup>
import { ref } from 'vue'

defineProps({
  nodes: { type: Array, required: true },
  selectedId: { type: String, default: '' },
  defaultExpand: { type: Boolean, default: true },
})
const emit = defineEmits(['select'])

const expanded = ref({})

function isExpanded(node) {
  return expanded.value[node.id] ?? true
}
function toggle(node) {
  expanded.value[node.id] = !isExpanded(node)
}

const STATUS_META = {
  pending: { label: '待写', cls: 'bg-slate-200 text-slate-500' },
  generating: { label: '生成中', cls: 'bg-blue-100 text-blue-600' },
  completed: { label: '已草拟', cls: 'bg-amber-100 text-amber-700' },
  reviewed: { label: '已校审', cls: 'bg-emerald-100 text-emerald-700' },
}
</script>

<template>
  <div class="text-sm">
    <template v-for="node in nodes" :key="node.id">
      <!-- 节点行 -->
      <div
        class="group flex items-center gap-1 px-2 py-[5px] rounded cursor-pointer border-l-2 transition"
        :class="selectedId === node.id
          ? 'bg-blue-50 border-blue-500 text-blue-700'
          : 'border-transparent hover:bg-slate-50 text-slate-700'"
        :style="{ paddingLeft: `${(node.level - 1) * 14 + 8}px` }"
        @click="emit('select', node)"
      >
        <button
          v-if="node.children && node.children.length"
          class="w-4 h-4 flex items-center justify-center text-slate-400 hover:text-slate-600 shrink-0"
          @click.stop="toggle(node)"
        >
          <el-icon :size="12">
            <ArrowRight v-if="!isExpanded(node)" />
            <ArrowDown v-else />
          </el-icon>
        </button>
        <span v-else class="w-4 shrink-0" />
        <span class="flex-1 truncate leading-5" :title="node.title">{{ node.title }}</span>
        <span
          v-if="node.word_budget"
          class="text-[10px] text-slate-400 shrink-0 hidden group-hover:inline mr-1"
        >{{ node.word_budget }}字</span>
        <span
          class="text-[10px] px-1.5 py-0.5 rounded shrink-0"
          :class="STATUS_META[node.status]?.cls || STATUS_META.pending.cls"
        >
          {{ STATUS_META[node.status]?.label || node.status }}
        </span>
      </div>
      <!-- 子节点 -->
      <template v-if="isExpanded(node)">
        <OutlineTree
          v-if="node.children && node.children.length"
          :nodes="node.children"
          :selected-id="selectedId"
          @select="emit('select', $event)"
        />
      </template>
    </template>
  </div>
</template>
