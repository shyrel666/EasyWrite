import { afterEach, describe, expect, it } from 'vitest'
import { cycleTheme, initTheme, isDark, setTheme, themePref } from '@/utils/theme'

describe('theme', () => {
  afterEach(() => {
    localStorage.clear()
    document.documentElement.classList.remove('dark')
  })

  it('手动选择深色：html 加 dark 类并记住选择', () => {
    setTheme('dark')
    expect(isDark.value).toBe(true)
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(localStorage.getItem('easywrite.theme')).toBe('dark')
  })

  it('初始化时读取已保存的选择，无效值回退到跟随系统', () => {
    localStorage.setItem('easywrite.theme', 'light')
    initTheme()
    expect(themePref.value).toBe('light')
    expect(document.documentElement.classList.contains('dark')).toBe(false)

    localStorage.setItem('easywrite.theme', 'purple')
    initTheme()
    expect(themePref.value).toBe('system')
  })

  it('依次切换：跟随系统 → 浅色 → 深色 → 跟随系统', () => {
    setTheme('system')
    cycleTheme()
    expect(themePref.value).toBe('light')
    cycleTheme()
    expect(themePref.value).toBe('dark')
    cycleTheme()
    expect(themePref.value).toBe('system')
  })
})
