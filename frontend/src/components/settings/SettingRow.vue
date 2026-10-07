<script setup>
/** 设置项一行：左侧名称与说明，右侧控件；窄屏上下排列。放在 divide-y 容器里成组。 */
defineProps({
  label: { type: String, required: true },
  hint: { type: String, default: '' },
})
</script>

<template>
  <div class="grid gap-x-8 gap-y-2.5 px-5 sm:px-6 py-4 md:grid-cols-[minmax(0,13rem)_minmax(0,1fr)]">
    <div class="min-w-0">
      <p class="text-[13px] font-medium text-ink flex items-center gap-2 flex-wrap">
        {{ label }}
        <slot name="badge" />
      </p>
      <p v-if="hint || $slots.hint" class="hint mt-1"><slot name="hint">{{ hint }}</slot></p>
    </div>
    <div class="min-w-0 self-center">
      <slot />
    </div>
  </div>
</template>
