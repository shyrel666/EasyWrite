<script setup>
import { ref } from 'vue'
import { sectionStatus } from '@/utils/project'

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
</script>

<template>
  <div class="text-[13px]">
    <template v-for="node in nodes" :key="node.id">
      <div
        class="group relative flex items-center gap-1.5 pr-2 py-[5px] mx-1.5 rounded-md cursor-pointer transition-colors"
        :class="selectedId === node.id
          ? 'bg-accent-soft text-accent-fg'
          : node.level === 1 ? 'text-ink hover:bg-sunken' : 'text-ink-2 hover:bg-sunken hover:text-ink'"
        :style="{ paddingLeft: `${(node.level - 1) * 14 + 6}px` }"
        @click="emit('select', node)"
      >
        <span v-if="selectedId === node.id" class="absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-full bg-accent" />
        <button
          v-if="node.children && node.children.length"
          class="w-4 h-4 flex items-center justify-center text-ink-3 hover:text-ink shrink-0 rounded"
          @click.stop="toggle(node)"
        >
          <el-icon :size="11" class="transition-transform" :class="isExpanded(node) ? 'rotate-90' : ''"><ArrowRight /></el-icon>
        </button>
        <span v-else class="w-4 shrink-0" />
        <span
          class="w-[7px] h-[7px] rounded-full shrink-0"
          :class="sectionStatus(node.status).dot"
          :title="sectionStatus(node.status).label"
        />
        <span class="flex-1 truncate leading-5" :class="{ 'font-medium': node.level === 1 }" :title="node.title">{{ node.title }}</span>
        <span
          v-if="node.word_budget"
          class="text-2xs text-ink-3 shrink-0 hidden group-hover:inline num"
        >{{ node.word_budget }}字</span>
      </div>
      <div v-if="isExpanded(node) && node.children && node.children.length" :class="{ 'mb-1': node.level === 1 }">
        <OutlineTree
          :nodes="node.children"
          :selected-id="selectedId"
          @select="emit('select', $event)"
        />
      </div>
    </template>
  </div>
</template>
