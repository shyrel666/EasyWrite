<script setup>
import { setTheme, themePref } from '@/utils/theme'

/** 外观：主题三选一，每项带一张缩略界面预览（预览色值固定，不随当前主题变化）。 */
const PALETTE = {
  light: { paper: '#F4F2ED', side: '#EEEBE4', card: '#FFFFFF', line: '#E5E1D8', ink: '#1F1E1B', mute: '#D0CBC0', accent: '#26487A' },
  dark: { paper: '#16171A', side: '#1A1B1F', card: '#1E2024', line: '#2E3137', ink: '#EAE7E0', mute: '#42464E', accent: '#5683C6' },
}

const OPTIONS = [
  { key: 'system', label: '跟随系统', text: '随操作系统的明暗设置自动切换', halves: ['light', 'dark'] },
  { key: 'light', label: '浅色', text: '纸色底、墨色字，适合白天与打印校对', halves: ['light'] },
  { key: 'dark', label: '深色', text: '夜间撰写更护眼，标书纸面保持浅色', halves: ['dark'] },
]

function vars(name) {
  const p = PALETTE[name]
  return Object.fromEntries(Object.entries(p).map(([k, v]) => [`--p-${k}`, v]))
}
</script>

<template>
  <div class="card">
    <div class="px-5 sm:px-6 pt-5 pb-1">
      <h3 class="text-[13px] font-medium text-ink">界面主题</h3>
      <p class="hint mt-1">选择记在本浏览器，侧栏底部的「外观」也可以快速切换。</p>
    </div>
    <div class="p-5 sm:p-6 grid sm:grid-cols-3 gap-3">
      <button
        v-for="o in OPTIONS"
        :key="o.key"
        type="button"
        class="theme-card"
        :class="{ 'is-active': themePref === o.key }"
        :aria-pressed="themePref === o.key"
        @click="setTheme(o.key)"
      >
        <span class="preview">
          <span
            v-for="(h, i) in o.halves"
            :key="h"
            class="mock"
            :style="[vars(h), o.halves.length > 1 ? { clipPath: i === 0 ? 'polygon(0 0, 62% 0, 38% 100%, 0 100%)' : 'polygon(62% 0, 100% 0, 100% 100%, 38% 100%)' } : null]"
          >
            <span class="mock-side">
              <span class="w-3 h-3 rounded-[3px]" style="background: var(--p-accent)" />
              <span class="mock-bar w-9" />
              <span class="mock-bar w-7" />
              <span class="mock-bar w-8" />
            </span>
            <span class="mock-main">
              <span class="mock-bar w-16 !h-[5px]" style="background: var(--p-ink)" />
              <span class="mock-card">
                <span class="mock-bar w-full" />
                <span class="mock-bar w-4/5" />
                <span class="mock-bar w-3/5" />
                <span class="mt-auto self-end w-7 h-[7px] rounded-[2px]" style="background: var(--p-accent)" />
              </span>
            </span>
          </span>
        </span>
        <span class="flex items-center justify-between gap-2 mt-3">
          <span class="text-[13px] font-medium text-ink">{{ o.label }}</span>
          <el-icon v-if="themePref === o.key" class="text-accent"><CircleCheckFilled /></el-icon>
        </span>
        <span class="block hint mt-0.5">{{ o.text }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.theme-card {
  @apply flex flex-col text-left rounded-xl border border-line bg-surface p-2.5 pb-3 transition;
}
.theme-card:hover {
  @apply border-line-strong;
}
.theme-card.is-active {
  @apply border-accent;
  box-shadow: 0 0 0 3px rgb(var(--c-accent) / 0.12);
}
.theme-card > span:not(.preview) {
  @apply px-1;
}
.preview {
  @apply relative block h-24 rounded-lg overflow-hidden border border-line;
}
.mock {
  @apply absolute inset-0 flex;
  background: var(--p-paper);
}
.mock-side {
  @apply w-[30%] h-full flex flex-col gap-[5px] p-2;
  background: var(--p-side);
  border-right: 1px solid var(--p-line);
}
.mock-main {
  @apply flex-1 flex flex-col gap-2 p-2.5;
}
.mock-card {
  @apply flex-1 flex flex-col gap-[5px] rounded-[4px] p-2;
  background: var(--p-card);
  border: 1px solid var(--p-line);
}
.mock-bar {
  @apply block h-[3px] rounded-full;
  background: var(--p-mute);
}
</style>
