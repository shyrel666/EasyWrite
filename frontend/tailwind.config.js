/** @type {import('tailwindcss').Config} */

// 语义色全部来自 main.css 中的 CSS 变量（RGB 通道），明暗主题只切换变量，组件里不写死色值
const token = (name) => `rgb(var(--c-${name}) / <alpha-value>)`

export default {
  content: ['./index.html', './src/**/*.{vue,js}'],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          '"Segoe UI Variable Text"', '"Segoe UI"', 'system-ui', '-apple-system', '"PingFang SC"',
          '"HarmonyOS Sans SC"', '"Microsoft YaHei UI"', '"Microsoft YaHei"', 'sans-serif',
        ],
        serif: ['"Noto Serif SC"', '"Source Han Serif SC"', '"Songti SC"', 'STSong', 'SimSun', 'serif'],
        kai: ['"Kaiti SC"', 'STKaiti', 'KaiTi', '楷体', 'serif'],
        mono: ['"Cascadia Code"', 'Consolas', '"SF Mono"', 'Menlo', 'monospace'],
      },
      colors: {
        paper: token('paper'),
        surface: token('surface'),
        raised: token('raised'),
        sunken: token('sunken'),
        line: { DEFAULT: token('line'), strong: token('line-strong') },
        ink: { DEFAULT: token('ink'), 2: token('ink-2'), 3: token('ink-3') },
        accent: { DEFAULT: token('accent'), soft: token('accent-soft'), fg: token('accent-fg') },
        seal: token('seal'),
        ok: token('ok'),
        warn: token('warn'),
        bad: token('bad'),
      },
      boxShadow: {
        sheet: '0 1px 2px rgb(var(--c-shadow) / 0.06), 0 8px 24px -12px rgb(var(--c-shadow) / 0.18)',
        float: '0 12px 32px -8px rgb(var(--c-shadow) / 0.28), 0 2px 6px rgb(var(--c-shadow) / 0.08)',
      },
      fontSize: {
        '2xs': ['11px', '16px'],
      },
    },
  },
  plugins: [],
}
