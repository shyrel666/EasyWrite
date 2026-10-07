<script setup>
defineProps({
  eyebrow: { type: String, default: '' },
  title: { type: String, required: true },
  description: { type: String, default: '' },
})
</script>

<template>
  <header class="flex items-end justify-between gap-x-6 gap-y-4 flex-wrap mb-6 sm:mb-8 rise">
    <div class="min-w-0">
      <p v-if="eyebrow" class="eyebrow mb-2">{{ eyebrow }}</p>
      <h1 class="text-[22px] sm:text-[26px] font-semibold tracking-tight leading-tight text-ink">{{ title }}</h1>
      <p v-if="description || $slots.description" class="mt-2 text-sm text-ink-2 max-w-2xl leading-relaxed">
        <slot name="description">{{ description }}</slot>
      </p>
    </div>
    <div v-if="$slots.actions" class="flex items-center gap-2 flex-wrap">
      <slot name="actions" />
    </div>
  </header>
</template>
