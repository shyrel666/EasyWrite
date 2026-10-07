import { ref } from 'vue'

/**
 * 明暗主题：'system' 跟随系统，'light' / 'dark' 为手动指定；选择记在本浏览器。
 * 在 main.js 挂载前调用 initTheme()，避免首屏闪白。
 */
const KEY = 'easywrite.theme'
const media = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null

export const themePref = ref('system')
export const isDark = ref(false)

function read() {
  try {
    const v = localStorage.getItem(KEY)
    return ['light', 'dark', 'system'].includes(v) ? v : 'system'
  } catch {
    return 'system'
  }
}

function apply() {
  isDark.value = themePref.value === 'dark' || (themePref.value === 'system' && !!media?.matches)
  document.documentElement.classList.toggle('dark', isDark.value)
}

export function initTheme() {
  themePref.value = read()
  apply()
  media?.addEventListener?.('change', apply)
}

export function setTheme(pref) {
  themePref.value = pref
  try {
    localStorage.setItem(KEY, pref)
  } catch { /* 存储不可用时只在本次会话生效 */ }
  apply()
}

/** 依次切换：跟随系统 → 浅色 → 深色 */
export function cycleTheme() {
  const order = ['system', 'light', 'dark']
  setTheme(order[(order.indexOf(themePref.value) + 1) % order.length])
}
