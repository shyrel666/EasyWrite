<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAiStore } from '@/stores/ai'
import { useProjectStore } from '@/stores/project'
import { useTaskStore } from '@/stores/tasks'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import TaskCenter from '@/components/layout/TaskCenter.vue'
import ModeBanner from '@/components/common/ModeBanner.vue'
import BrandMark from '@/components/common/BrandMark.vue'
import { STAGES, stageIndex, stageLabel } from '@/utils/project'
import { cycleTheme, themePref } from '@/utils/theme'

const props = defineProps({
  projectId: { type: String, default: '' },
  projectName: { type: String, default: '' },
  active: { type: String, default: '' },
  exportEnabled: { type: Boolean, default: false },
  // 内容区撑满视口、自行管理滚动（编纂台三栏布局）
  fill: { type: Boolean, default: false },
})

const route = useRoute()
const router = useRouter()
const ai = useAiStore()
const projectStore = useProjectStore()
const taskStore = useTaskStore()

const globalNav = [
  { name: 'dashboard', path: '/', label: '工作台', icon: 'House' },
  { name: 'knowledge', path: '/knowledge', label: '知识库', icon: 'Collection' },
  { name: 'assets', path: '/assets', label: '企业资产', icon: 'Suitcase' },
  { name: 'templates', path: '/templates', label: '排版模板', icon: 'Brush' },
]

const projectNav = computed(() =>
  props.projectId
    ? [
        { name: 'wizard', path: `/project/${props.projectId}/wizard`, label: '项目向导', icon: 'Guide' },
        { name: 'workspace', path: `/project/${props.projectId}/workspace`, label: '标书编纂', icon: 'EditPen' },
        { name: 'deviation', path: `/project/${props.projectId}/deviation`, label: '技术偏离表', icon: 'List' },
        { name: 'quality', path: `/project/${props.projectId}/quality`, label: '质检与合规', icon: 'CircleCheck' },
      ]
    : []
)

const stage = computed(() => (projectStore.id === props.projectId ? projectStore.stage : ''))

function isActive(name) {
  if (props.active === name) return true
  return route.name === name
}

// ---- 侧栏：桌面端可收起为图标栏（记在本浏览器），移动端为抽屉 ----
const COLLAPSE_KEY = 'easywrite.sidebarCollapsed'
function readCollapsed() {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === '1'
  } catch {
    return false
  }
}
const collapsed = ref(readCollapsed())
const mobileOpen = ref(false)
const hideC = computed(() => (collapsed.value ? 'md:hidden' : ''))
const rowC = computed(() => (collapsed.value ? 'md:justify-center md:px-0' : ''))

function toggleCollapse() {
  collapsed.value = !collapsed.value
  try {
    localStorage.setItem(COLLAPSE_KEY, collapsed.value ? '1' : '0')
  } catch { /* 忽略 */ }
}

function go(path) {
  mobileOpen.value = false
  router.push(path)
}

watch(() => route.fullPath, () => { mobileOpen.value = false })

const THEME_META = {
  system: { label: '跟随系统', icon: 'Monitor' },
  light: { label: '浅色', icon: 'Sunny' },
  dark: { label: '深色', icon: 'Moon' },
}
const themeMeta = computed(() => THEME_META[themePref.value])

// ---- 导出：选择 Word 排版模板（记住本浏览器上次的选择） ----
const TEMPLATE_KEY = 'easywrite.exportTemplate'
const exportDialog = ref(false)
const templates = ref([])
const templateId = ref('gov_standard')

// 导出前检查清单：只提示、不阻止导出
const preflight = ref(null)
const preflightLoading = ref(false)
const openChecks = ref(new Set())
const PREFLIGHT_ICON = {
  ok: { icon: 'CircleCheckFilled', cls: 'text-ok' },
  warn: { icon: 'WarningFilled', cls: 'text-warn' },
  unknown: { icon: 'QuestionFilled', cls: 'text-ink-3' },
}

async function loadPreflight() {
  preflightLoading.value = true
  openChecks.value = new Set()
  try {
    preflight.value = await api.exportPreflight(props.projectId)
  } catch (e) {
    preflight.value = null
    ElMessage.error('导出前检查失败：' + e.message)
  } finally {
    preflightLoading.value = false
  }
}

function toggleCheck(key) {
  const next = new Set(openChecks.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  openChecks.value = next
}

function locateSection(sectionId) {
  exportDialog.value = false
  router.push({ path: `/project/${props.projectId}/workspace`, query: { section: sectionId } })
}

async function openExport() {
  mobileOpen.value = false
  exportDialog.value = true
  loadPreflight()
  try {
    templateId.value = localStorage.getItem(TEMPLATE_KEY) || 'gov_standard'
  } catch { /* 存储不可用时使用默认模板 */ }
  try {
    templates.value = (await api.templates()).templates
    if (!templates.value.some((t) => t.id === templateId.value)) {
      templateId.value = templates.value[0]?.id || 'gov_standard'
    }
  } catch (e) {
    ElMessage.error('模板列表加载失败：' + e.message)
  }
}

function themeColor(t) {
  return t.theme_rgb ? `rgb(${t.theme_rgb.join(',')})` : (t.theme_color || '#003366')
}

// 架构图先在浏览器里用 Mermaid 渲染成图片随导出请求提交（Mermaid 按需加载，不拖慢其他页面）
const exporting = ref(false)
const exportProgress = ref('')

async function exportWord() {
  try {
    localStorage.setItem(TEMPLATE_KEY, templateId.value)
  } catch { /* 忽略 */ }
  exporting.value = true
  try {
    exportProgress.value = '读取标书内容…'
    const project = await api.getProject(props.projectId)
    const { renderOutlineDiagrams } = await import('@/utils/mermaid')
    const { diagrams, failed, total } = await renderOutlineDiagrams(project.outline, (i, n) => {
      exportProgress.value = `渲染架构图 ${i}/${n}…`
    })
    exportProgress.value = '生成 Word…'
    await api.exportBid(props.projectId, templateId.value, diagrams)
    exportDialog.value = false
    if (failed) {
      ElMessage.warning(`已导出技术标书 Word；${failed} 张架构图代码有误未能渲染，文档中已留出插图位置`)
    } else {
      ElMessage.success(total ? `已导出技术标书 Word（含 ${total} 张架构图）` : '已导出技术标书 Word（含目录与页码）')
    }
  } catch (e) {
    ElMessage.error('导出失败：' + e.message)
  } finally {
    exporting.value = false
    exportProgress.value = ''
  }
}
</script>

<template>
  <div class="h-[100dvh] flex overflow-hidden bg-paper text-ink">
    <!-- 移动端抽屉遮罩 -->
    <transition
      enter-active-class="transition-opacity duration-200"
      leave-active-class="transition-opacity duration-150"
      enter-from-class="opacity-0"
      leave-to-class="opacity-0"
    >
      <div v-if="mobileOpen" class="md:hidden fixed inset-0 z-40 bg-black/35" @click="mobileOpen = false" />
    </transition>

    <!-- ======= 侧栏 ======= -->
    <aside
      class="fixed md:static inset-y-0 left-0 z-50 w-[252px] flex flex-col shrink-0 bg-sunken border-r border-line transition-[transform,width] duration-200 ease-out md:translate-x-0 md:shadow-none"
      :class="[mobileOpen ? 'translate-x-0 shadow-float' : '-translate-x-full', collapsed ? 'md:w-[64px]' : 'md:w-[232px]']"
    >
      <!-- 品牌 -->
      <div class="h-[60px] flex items-center gap-2.5 px-4 shrink-0" :class="collapsed ? 'md:justify-center md:px-0' : ''">
        <button class="brand-btn" title="工作台" @click="go('/')"><BrandMark :size="32" /></button>
        <div class="min-w-0 flex-1" :class="hideC">
          <p class="text-[15px] font-semibold tracking-tight leading-none text-ink">EasyWrite</p>
          <p class="text-2xs text-ink-3 mt-1.5 leading-none tracking-wider">技术标编纂台</p>
        </div>
        <button class="icon-btn !w-7 !h-7 hidden md:inline-flex" :class="hideC" title="收起侧栏" @click="toggleCollapse">
          <el-icon><Fold /></el-icon>
        </button>
        <button class="icon-btn !w-7 !h-7 md:hidden" @click="mobileOpen = false"><el-icon><Close /></el-icon></button>
      </div>

      <nav class="flex-1 overflow-y-auto overflow-x-hidden px-2.5 pt-1 pb-3">
        <p class="nav-group mt-2" :class="hideC">工作区</p>
        <div class="space-y-0.5">
          <button
            v-for="item in globalNav"
            :key="item.name"
            class="side-row w-full"
            :class="[{ 'is-active': isActive(item.name) }, rowC]"
            :title="item.label"
            @click="go(item.path)"
          >
            <el-icon :size="17"><component :is="item.icon" /></el-icon>
            <span :class="hideC">{{ item.label }}</span>
          </button>
        </div>

        <template v-if="projectId">
          <div class="my-4 mx-2 border-t border-line" :class="collapsed ? 'hidden md:block' : 'hidden'" />
          <p class="nav-group mt-6" :class="hideC">当前项目</p>
          <div class="mx-0.5 mb-2 rounded-lg border border-line bg-surface/60 px-3 py-2.5" :class="hideC">
            <p class="text-[13px] font-medium leading-snug line-clamp-2 text-ink" :title="projectName">{{ projectName || '加载中…' }}</p>
            <template v-if="stage">
              <div class="mt-2.5 flex items-center gap-1">
                <span
                  v-for="(s, i) in STAGES"
                  :key="s.key"
                  class="h-1 flex-1 rounded-full"
                  :class="i <= stageIndex(stage) ? 'bg-accent' : 'bg-line-strong'"
                  :title="s.label"
                />
              </div>
              <p class="text-2xs text-ink-3 mt-1.5">{{ stageLabel(stage) }}</p>
            </template>
          </div>
          <div class="space-y-0.5">
            <button
              v-for="item in projectNav"
              :key="item.name"
              class="side-row w-full"
              :class="[{ 'is-active': isActive(item.name) }, rowC]"
              :title="item.label"
              @click="go(item.path)"
            >
              <el-icon :size="17"><component :is="item.icon" /></el-icon>
              <span :class="hideC">{{ item.label }}</span>
            </button>
          </div>
          <button
            v-if="exportEnabled"
            class="mt-3 w-full h-9 rounded-lg bg-accent text-white text-[13px] font-medium flex items-center justify-center gap-2 hover:opacity-90 transition shadow-sm"
            title="导出标书 Word"
            @click="openExport"
          >
            <el-icon :size="16"><Download /></el-icon>
            <span :class="hideC">导出标书 Word</span>
          </button>
        </template>
      </nav>

      <!-- 底部：任务 / 设置 / 外观 -->
      <div class="shrink-0 border-t border-line px-2.5 py-2.5 space-y-0.5">
        <TaskCenter :collapsed="collapsed" />
        <button class="side-row w-full" :class="[{ 'is-active': isActive('settings') }, rowC]" title="系统设置 · 模型配置" @click="go('/settings')">
          <span class="relative inline-flex">
            <el-icon :size="17"><Setting /></el-icon>
            <span
              class="absolute -top-0.5 -right-1 w-2 h-2 rounded-full ring-2 ring-sunken"
              :class="[ai.llmConfigured ? 'bg-ok' : 'bg-warn', collapsed ? 'hidden md:block' : 'hidden']"
            />
          </span>
          <span class="flex-1 min-w-0 flex items-center justify-between gap-2" :class="hideC">
            <span>设置</span>
            <span class="chip max-w-[118px]" :class="ai.llmConfigured ? 'chip-ok' : 'chip-warn'">
              <span class="dot" :class="ai.llmConfigured ? 'bg-ok' : 'bg-warn'" />
              <span class="truncate">{{ ai.llmConfigured ? ai.llmModel : '未配置模型' }}</span>
            </span>
          </span>
        </button>
        <button class="side-row w-full" :class="rowC" :title="`外观：${themeMeta.label}（点击切换）`" @click="cycleTheme">
          <el-icon :size="17"><component :is="themeMeta.icon" /></el-icon>
          <span class="flex-1 text-left" :class="hideC">外观</span>
          <span class="text-2xs text-ink-3" :class="hideC">{{ themeMeta.label }}</span>
        </button>
        <button class="side-row w-full" :class="[{ 'is-active': isActive('about') }, rowC]" title="关于 EasyWrite" @click="go('/about')">
          <el-icon :size="17"><InfoFilled /></el-icon>
          <span class="flex-1 text-left" :class="hideC">关于</span>
        </button>
        <button v-if="collapsed" class="side-row w-full hidden md:flex md:justify-center md:px-0" title="展开侧栏" @click="toggleCollapse">
          <el-icon :size="17"><Expand /></el-icon>
        </button>
      </div>
    </aside>

    <!-- ======= 主区 ======= -->
    <div class="flex-1 min-w-0 flex flex-col">
      <header class="md:hidden h-12 shrink-0 flex items-center gap-2 px-2 border-b border-line bg-surface">
        <button class="icon-btn" aria-label="打开导航" @click="mobileOpen = true"><el-icon :size="18"><Operation /></el-icon></button>
        <BrandMark :size="24" />
        <span class="text-sm font-semibold truncate flex-1 min-w-0">{{ projectId ? (projectName || '项目') : (route.meta.title || 'EasyWrite') }}</span>
        <span v-if="taskStore.activeCount" class="chip chip-accent"><el-icon class="animate-spin"><Loading /></el-icon>{{ taskStore.activeCount }}</span>
        <button v-if="exportEnabled" class="h-8 px-3 rounded-lg bg-accent text-white text-xs font-medium" @click="openExport">导出</button>
      </header>
      <ModeBanner v-if="!['settings', 'about'].includes(route.name)" />
      <main class="flex-1 min-h-0" :class="fill ? 'overflow-hidden' : 'overflow-y-auto'">
        <slot />
      </main>
    </div>

    <!-- ======= 导出模板选择 ======= -->
    <el-dialog v-model="exportDialog" title="导出标书 Word" width="min(600px, 94vw)" align-center>
      <section class="-mt-2 mb-5">
        <div class="flex items-center justify-between gap-2 mb-2">
          <h4 class="text-[13px] font-semibold text-ink">
            导出前检查
            <template v-if="preflight">
              <span v-if="preflight.ready" class="chip chip-ok ml-1.5 align-[1px]">全部通过</span>
              <span v-else class="chip chip-warn ml-1.5 align-[1px]">{{ preflight.attention }} 项需关注</span>
            </template>
          </h4>
          <button class="text-2xs text-ink-3 hover:text-ink inline-flex items-center gap-1" :disabled="preflightLoading" @click="loadPreflight">
            <el-icon :class="{ 'animate-spin': preflightLoading }"><Refresh /></el-icon>重新检查
          </button>
        </div>
        <div v-if="preflightLoading && !preflight" class="text-xs text-ink-3 py-4 text-center">正在检查…</div>
        <div v-else-if="preflight" class="rounded-lg border border-line divide-y divide-line max-h-[34vh] overflow-auto">
          <div v-for="c in preflight.checks" :key="c.key" class="px-3 py-2">
            <button
              class="w-full flex items-start gap-2 text-left"
              :class="c.details.length ? 'cursor-pointer' : 'cursor-default'"
              @click="c.details.length && toggleCheck(c.key)"
            >
              <el-icon class="mt-0.5 shrink-0" :class="PREFLIGHT_ICON[c.status].cls"><component :is="PREFLIGHT_ICON[c.status].icon" /></el-icon>
              <span class="flex-1 min-w-0">
                <span class="block text-xs font-medium text-ink">{{ c.title }}</span>
                <span class="block text-2xs text-ink-2 mt-0.5 leading-relaxed">{{ c.message }}</span>
              </span>
              <el-icon v-if="c.details.length" class="mt-0.5 text-ink-3 transition-transform" :class="{ 'rotate-90': openChecks.has(c.key) }"><ArrowRight /></el-icon>
            </button>
            <ul v-if="openChecks.has(c.key)" class="mt-1.5 ml-6 space-y-1">
              <li v-for="(d, i) in c.details" :key="i" class="flex items-start gap-2 text-2xs">
                <span class="min-w-0 flex-1 leading-relaxed">
                  <span class="text-ink">{{ d.title }}</span><span v-if="d.detail" class="text-ink-3"> · {{ d.detail }}</span>
                </span>
                <button v-if="d.section_id" class="shrink-0 text-accent-fg hover:underline" @click="locateSection(d.section_id)">定位</button>
              </li>
              <li v-if="c.details.length >= 50" class="text-2xs text-ink-3">仅列出前 50 项</li>
            </ul>
          </div>
        </div>
        <p class="hint mt-1.5">清单只作提示，不影响导出。</p>
      </section>

      <p class="text-xs text-ink-2 mb-3">选择排版模板（字体、主题色、页边距与页眉），可在「排版模板」中自定义。导出含封面、目录域与页码。</p>
      <div class="grid sm:grid-cols-2 gap-2.5 max-h-[30vh] overflow-auto p-0.5">
        <button
          v-for="t in templates"
          :key="t.id"
          class="text-left flex gap-3 p-2.5 rounded-xl border transition"
          :class="templateId === t.id ? 'border-accent ring-2 ring-accent/20 bg-accent-soft/40' : 'border-line hover:border-line-strong bg-surface'"
          @click="templateId = t.id"
        >
          <span class="w-11 h-[58px] shrink-0 rounded-[3px] bg-white border border-black/10 shadow-sm p-1.5 flex flex-col gap-[3px]">
            <span class="h-[2px] w-full" :style="{ background: themeColor(t) }" />
            <span class="h-[4px] w-3/4 mt-1 rounded-[1px]" :style="{ background: themeColor(t) }" />
            <span v-for="n in 4" :key="n" class="h-[2px] rounded-[1px] bg-black/15" :class="n === 4 ? 'w-2/3' : 'w-full'" />
          </span>
          <span class="min-w-0">
            <span class="block text-[13px] font-medium text-ink">{{ t.name }}</span>
            <span v-if="t.description" class="block text-2xs text-ink-3 mt-1 leading-relaxed line-clamp-3">{{ t.description }}</span>
          </span>
        </button>
      </div>
      <template #footer>
        <el-button :disabled="exporting" @click="exportDialog = false">取消</el-button>
        <el-button type="primary" :disabled="!templateId" :loading="exporting" @click="exportWord">
          {{ exporting ? exportProgress : '导出 Word' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.brand-btn {
  @apply flex shrink-0 rounded-lg select-none;
  box-shadow: 0 1px 2px rgb(var(--c-shadow) / 0.18);
}
/* 悬停时朱砂方印"落一下" */
.brand-btn:hover :deep(.brand-seal) {
  transform: rotate(-16deg) scale(1.12);
}
.nav-group {
  @apply px-2.5 mb-1.5 text-2xs font-semibold tracking-[0.16em] text-ink-3;
}
</style>
