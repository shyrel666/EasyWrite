<script setup>
defineProps({
  icon: { type: String, default: 'Document' },
  title: { type: String, default: '暂无数据' },
  description: { type: String, default: '' },
})
</script>

<template>
  <div class="flex flex-col items-center justify-center py-14 text-center">
    <el-icon :size="44" class="text-slate-300"><component :is="icon" /></el-icon>
    <p class="mt-3 text-sm font-medium text-slate-600">{{ title }}</p>
    <p v-if="description" class="mt-1 text-xs text-slate-400 max-w-xs">{{ description }}</p>
    <div v-if="$slots.default" class="mt-4">
      <slot />
    </div>
  </div>
</template>
