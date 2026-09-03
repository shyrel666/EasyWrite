<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAiStore } from '@/stores/ai'
import { ElMessage } from 'element-plus'

const props = defineProps({
  projectId: { type: String, default: '' },
  projectName: { type: String, default: '' },
  active: { type: String, default: '' },
  exportEnabled: { type: Boolean, default: false },
})

const route = useRoute()
const router = useRouter()
const ai = useAiStore()

const globalNav = [
  { name: 'dashboard', path: '/', label: '工作台' },
  { name: 'knowledge', path: '/knowledge', label: '知识库' },
  { name: 'assets', path: '/assets', label: '企业资产' },
  { name: 'templates', path: '/templates', label: '模板中心' },
]

const projectNav = computed(() =>
  props.projectId
    ? [
        { name: 'workspace', path: `/project/${props.projectId}/workspace`, label: '标书编纂' },
        { name: 'deviation', path: `/project/${props.projectId}/deviation`, label: '技术偏离表' },
        { name: 'quality', path: `/project/${props.projectId}/quality`, label: '质检与合规' },
      ]
    : []
)

const mobileNav = computed(() => [
  { name: 'dashboard', path: '/', label: '首页', icon: 'HomeFilled' },
  ...(props.projectId
    ? [
        { name: 'workspace', path: `/project/${props.projectId}/workspace`, label: '编纂', icon: 'EditPen' },
        { name: 'deviation', path: `/project/${props.projectId}/deviation`, label: '偏离', icon: 'List' },
        { name: 'quality', path: `/project/${props.projectId}/quality`, label: '质检', icon: 'CircleCheck' },
      ]
    : []),
  { name: 'knowledge', path: '/knowledge', label: '知识库', icon: 'Collection' },
])

function isActive(name) {
  if (props.active === name) return true
  return route.name === name
}

function exportWord() {
  const url = `/api/v1/project/${props.projectId}/export?template_id=gov_standard`
  window.open(url, '_blank')
  ElMessage.success('已开始下载技术标书 Word（含目录与页码）')
}
</script>

<template>
  <div class="flex flex-col min-h-screen">
    <!-- ======= 顶部导航（桌面） ======= -->
    <header class="bg-slate-900 text-white shadow-md z-30 shrink-0 hidden md:block">
      <div class="flex items-center justify-between px-4 lg:px-6 h-14">
        <div class="flex items-center gap-6 min-w-0">
          <!-- Logo -->
          <div class="flex items-center gap-2.5 cursor-pointer shrink-0" @click="router.push('/')">
            <div class="w-8 h-8 rounded bg-blue-600 flex items-center justify-center font-bold text-white shadow">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <div class="font-bold tracking-wide leading-tight">
              EasyWrite
              <span class="hidden lg:inline text-[11px] px-2 py-0.5 ml-1 rounded bg-slate-800 text-slate-300 border border-slate-700">技术标 Copilot</span>
            </div>
          </div>

          <!-- 项目上下文 -->
          <div v-if="projectId" class="flex items-center gap-2 min-w-0 text-sm text-slate-300">
            <el-icon><FolderOpened /></el-icon>
            <span class="truncate max-w-[16rem] font-medium" :title="projectName">{{ projectName || '未命名项目' }}</span>
          </div>

          <!-- 全局导航 -->
          <nav class="flex items-center gap-1 text-sm font-medium">
            <button
              v-for="item in globalNav"
              :key="item.name"
              class="px-3 py-1.5 rounded-md transition whitespace-nowrap"
              :class="isActive(item.name) ? 'bg-blue-600 text-white' : 'text-slate-300 hover:text-white hover:bg-slate-800'"
              @click="router.push(item.path)"
            >
              {{ item.label }}
            </button>
          </nav>
        </div>

        <div class="flex items-center gap-3 text-xs shrink-0">
          <button
            class="flex items-center gap-1.5 px-2.5 py-1 rounded border border-slate-700 text-slate-300 hover:bg-slate-800 transition"
            title="AI 模型状态"
            @click="router.push('/settings')"
          >
            <span
              class="w-2 h-2 rounded-full"
              :class="ai.llmConfigured ? 'bg-emerald-400' : 'bg-amber-400'"
            />
            <span class="hidden lg:inline">{{ ai.llmConfigured ? ai.llmModel : '未配置模型' }}</span>
          </button>
          <button
            v-if="exportEnabled"
            class="bg-emerald-600 hover:bg-emerald-700 text-white font-semibold px-3 py-1.5 rounded shadow transition"
            @click="exportWord"
          >
            导出标书 Word
          </button>
        </div>
      </div>

      <!-- 项目子导航 -->
      <div v-if="projectNav.length" class="bg-slate-800 border-t border-slate-700">
        <div class="flex items-center gap-1 px-4 lg:px-6 h-10 text-sm">
          <button
            v-for="item in projectNav"
            :key="item.name"
            class="px-3 py-1 rounded-md transition font-medium"
            :class="isActive(item.name) ? 'bg-slate-700 text-white' : 'text-slate-400 hover:text-white'"
            @click="router.push(item.path)"
          >
            {{ item.label }}
          </button>
        </div>
      </div>
    </header>

    <!-- ======= 移动端顶部栏 ======= -->
    <header class="bg-slate-900 text-white shadow-md z-30 shrink-0 md:hidden">
      <div class="flex items-center justify-between px-3 h-12">
        <button class="flex items-center gap-2" @click="router.push('/')">
          <div class="w-7 h-7 rounded bg-blue-600 flex items-center justify-center font-bold text-sm">E</div>
          <span class="font-bold text-sm">EasyWrite</span>
          <span v-if="projectId" class="text-[11px] text-slate-400 truncate max-w-[10rem]">{{ projectName }}</span>
        </button>
        <div class="flex items-center gap-2">
          <button
            class="flex items-center gap-1 px-2 py-1 rounded border border-slate-700 text-[11px]"
            @click="router.push('/settings')"
          >
            <span class="w-2 h-2 rounded-full" :class="ai.llmConfigured ? 'bg-emerald-400' : 'bg-amber-400'" />
          </button>
          <button
            v-if="exportEnabled"
            class="bg-emerald-600 text-white text-[11px] px-2 py-1 rounded"
            @click="exportWord"
          >
            导出
          </button>
        </div>
      </div>
    </header>

    <!-- ======= 内容区 ======= -->
    <main class="flex-1 min-h-0 pb-14 md:pb-0">
      <slot />
    </main>

    <!-- ======= 移动端底部标签栏 ======= -->
    <nav class="md:hidden fixed bottom-0 inset-x-0 bg-white border-t border-slate-200 z-40">
      <div class="grid" :style="{ gridTemplateColumns: `repeat(${mobileNav.length}, 1fr)` }">
        <button
          v-for="item in mobileNav"
          :key="item.name"
          class="flex flex-col items-center gap-0.5 py-1.5 text-[10px] transition"
          :class="isActive(item.name) ? 'text-blue-600' : 'text-slate-500'"
          @click="router.push(item.path)"
        >
          <el-icon :size="18"><component :is="item.icon" /></el-icon>
          {{ item.label }}
        </button>
      </div>
    </nav>
  </div>
</template>
