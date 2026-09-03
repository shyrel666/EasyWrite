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
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      }
    }
  },
  build: {
    // 生产构建产物直接输出到后端静态目录，uvicorn 单进程全托管
    outDir: path.resolve(__dirname, '../backend/app/static'),
    emptyOutDir: true
  }
})
