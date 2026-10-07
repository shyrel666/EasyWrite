<script setup>
import { computed } from 'vue'

/**
 * 服务商预设卡片：名称拆成主名 / 副名，括号里的说明作附注，底部显示接口域名。
 * 预设来自后端（生成模型与嵌入模型各一套），这里只负责展示与选择。
 */
const props = defineProps({
  presets: { type: Object, default: () => ({}) },
  modelValue: { type: String, default: '' },
  // 「不启用」预设（key 为 none）底部显示的文字；其他没有接口地址的预设提示自行填写
  noneLabel: { type: String, default: '不启用' },
})
const emit = defineEmits(['select'])

function splitName(name = '') {
  const m = name.match(/^(.*?)（(.*)）$/)
  const base = (m ? m[1] : name).trim()
  const note = m ? m[2] : ''
  const i = base.indexOf(' ')
  const primary = i > 0 ? base.slice(0, i) : base
  const secondary = i > 0 ? base.slice(i + 1) : ''
  return { primary, sub: [secondary, note].filter(Boolean).join(' · ') }
}

function hostOf(url) {
  if (!url) return ''
  try {
    const u = new URL(url)
    return u.port ? `${u.hostname}:${u.port}` : u.hostname
  } catch {
    return url
  }
}

const items = computed(() =>
  Object.entries(props.presets || {}).map(([key, p]) => ({
    key,
    ...splitName(p.name),
    host: hostOf(p.base_url),
    fallback: key === 'none' ? props.noneLabel : '自行填写接口地址',
  }))
)
</script>

<template>
  <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
    <button
      v-for="item in items"
      :key="item.key"
      type="button"
      class="provider-card"
      :class="{ 'is-active': modelValue === item.key }"
      :aria-pressed="modelValue === item.key"
      @click="emit('select', item.key)"
    >
      <span class="flex items-start gap-2">
        <span class="min-w-0 flex-1">
          <span class="block text-[13px] font-medium text-ink truncate">{{ item.primary }}</span>
          <span class="block min-h-4 text-2xs text-ink-3 truncate mt-0.5" :title="item.sub">{{ item.sub }}</span>
        </span>
        <span class="radio" />
      </span>
      <span class="mt-3 block text-2xs text-ink-3 truncate" :class="{ 'font-mono': item.host }">
        {{ item.host || item.fallback }}
      </span>
    </button>
  </div>
</template>

<style scoped>
.provider-card {
  @apply flex flex-col min-w-0 text-left rounded-lg border border-line bg-surface px-3 py-2.5 transition;
}
.provider-card:hover {
  @apply border-line-strong bg-raised;
}
.provider-card.is-active {
  @apply border-accent bg-accent-soft/60;
  box-shadow: 0 0 0 3px rgb(var(--c-accent) / 0.12);
}
.radio {
  @apply mt-0.5 w-3.5 h-3.5 shrink-0 rounded-full border border-line-strong bg-surface transition;
}
.provider-card.is-active .radio {
  @apply border-accent;
  border-width: 4px;
}
</style>
