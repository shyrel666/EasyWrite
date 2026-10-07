import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8790',
        changeOrigin: true
      },
      // 关于页：后端版本与接口文档
      '^/(health|docs|openapi\.json)': {
        target: 'http://127.0.0.1:8790',
        changeOrigin: true
      }
    }
  },
  build: {
    // 生产构建产物直接输出到后端静态目录，uvicorn 单进程全托管
    outDir: path.resolve(__dirname, '../backend/app/static'),
    emptyOutDir: true
  },
  test: {
    // npm test：src/**/__tests__/*.test.js，jsdom 模拟浏览器环境
    environment: 'jsdom',
    include: ['src/**/__tests__/**/*.test.js'],
    css: false
  }
})
