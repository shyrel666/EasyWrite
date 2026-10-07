<script setup>
defineProps({
  icon: { type: String, default: 'Document' },
  title: { type: String, default: '暂无数据' },
  description: { type: String, default: '' },
  compact: { type: Boolean, default: false },
})
</script>

<template>
  <div class="flex flex-col items-center justify-center text-center" :class="compact ? 'py-8' : 'py-14'">
    <div class="relative">
      <div class="absolute inset-0 translate-x-1.5 translate-y-1.5 rounded-2xl border border-dashed border-line-strong" />
      <div class="relative w-14 h-14 rounded-2xl bg-surface border border-line flex items-center justify-center text-ink-3 shadow-sheet">
        <el-icon :size="24"><component :is="icon" /></el-icon>
      </div>
    </div>
    <p class="mt-5 text-sm font-semibold text-ink">{{ title }}</p>
    <p v-if="description" class="mt-1.5 text-xs text-ink-3 max-w-sm leading-relaxed">{{ description }}</p>
    <div v-if="$slots.default" class="mt-5">
      <slot />
    </div>
  </div>
</template>
