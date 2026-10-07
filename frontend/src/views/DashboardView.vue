<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import AppShell from '@/components/layout/AppShell.vue'
import { useAiStore } from '@/stores/ai'
import { STAGES, projectEntry, stageIndex, stageLabel } from '@/utils/project'

const router = useRouter()
const ai = useAiStore()

const projects = ref([])
const kbStats = ref({ total_documents: 0, total_chunks: 0 })
const loading = ref(true)
const createVisible = ref(false)
const creating = ref(false)
const form = ref({ name: '', client_name: '', description: '' })
const keyword = ref('')

const now = new Date()
const greeting = (() => {
  const h = now.getHours()
  if (h < 5) return '夜深了'
  if (h < 11) return '早上好'
  if (h < 13) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
})()
const dateLabel = `${now.getMonth() + 1}月${now.getDate()}日 · 星期${'日一二三四五六'[now.getDay()]}`

const writingCount = computed(() => projects.value.filter((p) => p.stage === 'writing').length)
const filtered = computed(() => {
  const k = keyword.value.trim().toLowerCase()
  if (!k) return projects.value
  return projects.value.filter((p) => `${p.name} ${p.client_name || ''}`.toLowerCase().includes(k))
})

const STAGE_CHIP = { created: 'chip-mute', tender_analyzed: 'chip-warn', outline_confirmed: 'chip-accent', writing: 'chip-ok' }

const WORKFLOW = [
  { n: '01', title: '上传招标文件', text: '.docx / .pdf，自动识别章节结构' },
  { n: '02', title: '18 项结构化拆标', text: '评分细则、★号红线、资质门槛' },
  { n: '03', title: '按评分项规划大纲', text: '分值决定篇幅，每个评分项都有承接' },
  { n: '04', title: '撰写 · 偏离表 · 质检', text: '知识库检索撰写，导出 Word 前合规核查' },
]

async function load() {
  loading.value = true
  try {
    const [pl, ks] = await Promise.all([api.listProjects(), api.kbStats()])
    projects.value = pl
    kbStats.value = ks
  } catch (e) {
    ElMessage.error('加载失败：' + e.message)
  } finally {
    loading.value = false
  }
}

async function createProject() {
  if (!form.value.name.trim()) {
    ElMessage.warning('请填写项目名称')
    return
  }
  creating.value = true
  try {
    const project = await api.createProject(form.value)
    createVisible.value = false
    ElMessage.success('项目已创建，进入向导')
    router.push(`/project/${project.id}/wizard`)
  } catch (e) {
    ElMessage.error('创建失败：' + e.message)
  } finally {
    creating.value = false
  }
}

function enterProject(p) {
  router.push(projectEntry(p))
}

async function removeProject(p) {
  try {
    await ElMessageBox.confirm(`确认删除项目「${p.name}」？该操作不可恢复。`, '删除项目', { type: 'warning' })
  } catch { return }
  try {
    await api.deleteProject(p.id)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    ElMessage.error('删除失败：' + e.message)
  }
}

onMounted(load)
</script>

<template>
  <AppShell active="dashboard">
    <div class="max-w-6xl mx-auto px-4 sm:px-8 py-7 sm:py-12">
      <!-- 问候 -->
      <section class="rise flex flex-col sm:flex-row sm:items-end justify-between gap-5 mb-8 sm:mb-10">
        <div>
          <p class="eyebrow">{{ dateLabel }}</p>
          <h1 class="mt-2.5 text-[28px] sm:text-[34px] font-semibold tracking-tight leading-[1.2] text-ink">
            {{ greeting }}，今天写哪一份标书？
          </h1>
          <p class="mt-2 text-sm text-ink-2">
            <template v-if="loading">正在载入项目…</template>
            <template v-else-if="projects.length">共 <b class="num font-semibold text-ink">{{ projects.length }}</b> 个标书项目，其中 <b class="num font-semibold text-ink">{{ writingCount }}</b> 个正在撰写。</template>
            <template v-else>从一份招标文件开始，向导会带你走完拆标、大纲与事实设定。</template>
          </p>
        </div>
        <el-button type="primary" size="large" class="!h-11 !px-5 !rounded-xl" @click="createVisible = true">
          <el-icon class="mr-1.5"><Plus /></el-icon>新建标书项目
        </el-button>
      </section>

      <!-- 指标条 -->
      <section class="card grid grid-cols-2 lg:grid-cols-4 overflow-hidden mb-10 rise" style="animation-delay: 60ms">
        <div class="p-4 sm:p-5 border-b lg:border-b-0 border-r border-line">
          <p class="text-xs text-ink-3">标书项目</p>
          <p class="mt-2 text-[26px] font-semibold num leading-none">{{ projects.length }}</p>
          <p class="mt-2 text-2xs text-ink-3">撰写中 <span class="num">{{ writingCount }}</span></p>
        </div>
        <div class="p-4 sm:p-5 border-b lg:border-b-0 lg:border-r border-line">
          <p class="text-xs text-ink-3">知识库文档</p>
          <p class="mt-2 text-[26px] font-semibold num leading-none">{{ kbStats.total_documents || 0 }}</p>
          <p class="mt-2 text-2xs text-ink-3">知识切片 <span class="num">{{ kbStats.total_chunks || 0 }}</span></p>
        </div>
        <button class="p-4 sm:p-5 border-r border-line text-left hover:bg-raised transition" @click="router.push('/settings')">
          <p class="text-xs text-ink-3 flex items-center gap-1.5">
            <span class="dot" :class="ai.llmConfigured ? 'bg-ok' : 'bg-warn'" />生成模型
          </p>
          <p class="mt-2.5 text-[15px] font-semibold truncate" :class="ai.llmConfigured ? 'text-ink' : 'text-warn'">
            {{ ai.llmConfigured ? ai.llmModel : '未配置' }}
          </p>
          <p class="mt-1.5 text-2xs text-ink-3">{{ ai.llmConfigured ? '真实模型撰写' : '离线演示模式' }}</p>
        </button>
        <button class="p-4 sm:p-5 text-left hover:bg-raised transition" @click="router.push('/settings')">
          <p class="text-xs text-ink-3 flex items-center gap-1.5">
            <span class="dot" :class="ai.embeddingAvailable ? 'bg-ok' : 'bg-ink-3'" />语义检索
          </p>
          <p class="mt-2.5 text-[15px] font-semibold truncate">{{ ai.embeddingAvailable ? ai.embeddingModel : 'BM25 + 重排' }}</p>
          <p class="mt-1.5 text-2xs text-ink-3">{{ ai.embeddingAvailable ? '向量 + 关键词混合召回' : '未配置嵌入模型时的兜底' }}</p>
        </button>
      </section>

      <!-- 项目 -->
      <div class="flex items-center justify-between gap-3 mb-4 flex-wrap">
        <h2 class="text-base font-semibold text-ink">我的标书项目</h2>
        <el-input
          v-if="projects.length > 3"
          v-model="keyword"
          clearable
          placeholder="搜索项目或招标方"
          class="!w-56"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
      </div>

      <div v-if="loading" class="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <div v-for="i in 3" :key="i" class="h-52 rounded-xl skeleton" />
      </div>

      <!-- 空状态：工作流说明 -->
      <section v-else-if="!projects.length" class="card p-6 sm:p-8 rise">
        <div class="grid sm:grid-cols-2 lg:grid-cols-4 gap-px bg-line rounded-xl overflow-hidden border border-line">
          <div v-for="s in WORKFLOW" :key="s.n" class="bg-surface p-5">
            <p class="font-kai text-2xl text-seal leading-none">{{ s.n }}</p>
            <p class="mt-4 text-sm font-semibold text-ink">{{ s.title }}</p>
            <p class="mt-1.5 text-xs text-ink-3 leading-relaxed">{{ s.text }}</p>
          </div>
        </div>
        <div class="mt-6 flex items-center justify-between gap-4 flex-wrap">
          <p class="text-sm text-ink-2">还没有标书项目。新建后，向导会逐步引导你完成上述流程。</p>
          <el-button type="primary" @click="createVisible = true">创建第一个项目</el-button>
        </div>
      </section>

      <div v-else class="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <article
          v-for="(p, i) in filtered"
          :key="p.id"
          class="card group p-5 flex flex-col cursor-pointer transition hover:border-line-strong hover:shadow-sheet hover:-translate-y-0.5 rise"
          :style="{ animationDelay: `${Math.min(i, 8) * 40}ms` }"
          @click="enterProject(p)"
        >
          <div class="flex items-center justify-between gap-2">
            <span class="chip" :class="STAGE_CHIP[p.stage] || 'chip-mute'">{{ stageLabel(p.stage) }}</span>
            <button
              class="icon-btn !w-7 !h-7 opacity-0 group-hover:opacity-100 focus:opacity-100 hover:!text-bad"
              title="删除项目"
              @click.stop="removeProject(p)"
            >
              <el-icon><Delete /></el-icon>
            </button>
          </div>
          <h3 class="mt-3 text-[15px] font-semibold leading-snug line-clamp-2 text-ink">{{ p.name }}</h3>
          <p class="mt-1.5 text-xs text-ink-3 truncate">{{ p.client_name || '招标方未填写' }}</p>

          <div class="mt-auto pt-6">
            <div class="flex items-center gap-1.5 text-2xs">
              <template v-for="(s, si) in STAGES" :key="s.key">
                <span :class="si <= stageIndex(p.stage) ? 'text-ink font-medium' : 'text-ink-3'">{{ s.short }}</span>
                <span v-if="si < STAGES.length - 1" class="h-px flex-1" :class="si < stageIndex(p.stage) ? 'bg-accent' : 'bg-line'" />
              </template>
            </div>
            <div class="mt-4 flex items-center gap-3">
              <div class="meter flex-1"><span class="bg-accent" :style="{ width: `${Math.round(p.completion_rate || 0)}%` }" /></div>
              <span class="text-2xs num text-ink-2 font-medium w-9 text-right">{{ Math.round(p.completion_rate || 0) }}%</span>
            </div>
            <div class="mt-3 pt-3 border-t border-line flex items-center justify-between text-2xs text-ink-3">
              <span>更新于 {{ p.updated_at }}</span>
              <span class="text-accent-fg font-medium opacity-0 group-hover:opacity-100 transition flex items-center gap-0.5">
                {{ ['writing', 'outline_confirmed'].includes(p.stage) ? '进入编纂' : '继续向导' }}<el-icon><Right /></el-icon>
              </span>
            </div>
          </div>
        </article>

        <button
          v-if="!keyword"
          class="rounded-xl border border-dashed border-line-strong min-h-[13rem] flex flex-col items-center justify-center gap-2 text-ink-3 hover:text-accent-fg hover:border-accent/50 hover:bg-accent-soft/40 transition"
          @click="createVisible = true"
        >
          <el-icon :size="22"><Plus /></el-icon>
          <span class="text-sm font-medium">新建标书项目</span>
        </button>
        <p v-if="keyword && !filtered.length" class="sm:col-span-2 lg:col-span-3 text-center text-sm text-ink-3 py-10">没有匹配「{{ keyword }}」的项目</p>
      </div>
    </div>

    <!-- 新建项目 -->
    <el-dialog v-model="createVisible" title="新建标书项目" width="min(520px, 94vw)">
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="项目名称（标书全称）" required>
          <el-input v-model="form.name" placeholder="如：智慧园区一体化管理平台建设项目" maxlength="80" />
        </el-form-item>
        <el-form-item label="招标方 / 客户名称">
          <el-input v-model="form.client_name" placeholder="采购人单位全称" maxlength="60" />
        </el-form-item>
        <el-form-item label="项目背景描述（供大纲规划与撰写参考）">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="4"
            placeholder="简述建设内容、规模、核心需求；上传招标文件后系统会自动进行 18 项结构化拆解"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="createProject">创建并进入向导</el-button>
      </template>
    </el-dialog>
  </AppShell>
</template>
