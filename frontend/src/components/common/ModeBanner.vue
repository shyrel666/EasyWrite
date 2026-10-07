<script setup>
import { useAiStore } from '@/stores/ai'
import { useRouter } from 'vue-router'

const ai = useAiStore()
const router = useRouter()
</script>

<template>
  <div
    v-if="ai.loaded && ai.isMockMode"
    class="shrink-0 border-b border-warn/25 bg-warn/[0.08] text-ink text-xs px-4 py-2 flex items-center justify-center gap-x-3 gap-y-1 flex-wrap"
  >
    <span class="inline-flex items-center gap-1.5 font-semibold text-warn">
      <el-icon><WarningFilled /></el-icon>{{ ai.llmConfigured ? '上次生成已回退到演示模式' : '离线演示模式' }}
    </span>
    <span class="text-ink-2 hidden sm:inline">{{ ai.llmConfigured ? '模型已配置，但上次生成未获得真实输出。请测试连通性后重试。' : '未配置大模型 API Key，生成内容为内置演示样例，不代表真实 AI 输出。' }}</span>
    <span class="text-ink-2 sm:hidden">{{ ai.llmConfigured ? '上次生成内容为演示样例' : '生成内容为演示样例' }}</span>
    <button class="font-medium text-accent-fg hover:underline underline-offset-2 whitespace-nowrap" @click="router.push('/settings')">
      {{ ai.llmConfigured ? '检查模型配置 →' : '前往配置 →' }}
    </button>
  </div>
</template>
