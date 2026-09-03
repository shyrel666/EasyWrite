<script setup>
import { useAiStore } from '@/stores/ai'
import { useRouter } from 'vue-router'

const ai = useAiStore()
const router = useRouter()
</script>

<template>
  <div
    v-if="ai.loaded && ai.isMockMode"
    class="w-full bg-amber-100 border-b border-amber-300 text-amber-900 text-xs sm:text-sm px-3 sm:px-4 py-1.5 flex items-center justify-center gap-2 flex-wrap"
  >
    <el-icon class="text-amber-600"><WarningFilled /></el-icon>
    <span>
      当前为<b>离线演示模式</b>：未配置大模型 API Key，生成内容为内置演示样例（不代表真实 AI 输出）。
    </span>
    <button
      class="underline font-medium hover:text-amber-700 whitespace-nowrap"
      @click="router.push('/settings')"
    >
      前往配置 →
    </button>
  </div>
</template>
