<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import AppShell from '@/components/layout/AppShell.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { useAiStore } from '@/stores/ai'

const router = useRouter()
const ai = useAiStore()

const projects = ref([])
const kbStats = ref({ total_documents: 0, total_chunks: 0 })
const loading = ref(true)
const createVisible = ref(false)
const creating = ref(false)
const form = ref({ name: '', client_name: '', description: '' })

const STAGE_LABEL = {
  created: '待上传招标文件',
  tender_analyzed: '待规划大纲',
  outline_confirmed: '大纲已就绪',
  writing: '撰写中',
}

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
  if (p.stage === 'created' || p.stage === 'tender_analyzed') {
    router.push(`/project/${p.id}/wizard`)
  } else {
    router.push(`/project/${p.id}/workspace`)
  }
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
    <div class="max-w-6xl mx-auto px-4 sm:px-6 py-6">
      <!-- 统计栏 -->
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
        <div class="bg-white rounded-lg border border-slate-200 p-4">
          <p class="text-xs text-slate-500">标书项目</p>
          <p class="mt-1 text-2xl font-bold text-slate-800">{{ projects.length }}</p>
        </div>
        <div class="bg-white rounded-lg border border-slate-200 p-4">
          <p class="text-xs text-slate-500">知识库文档</p>
          <p class="mt-1 text-2xl font-bold text-slate-800">{{ kbStats.total_documents || 0 }}</p>
        </div>
        <div class="bg-white rounded-lg border border-slate-200 p-4">
          <p class="text-xs text-slate-500">知识切片</p>
          <p class="mt-1 text-2xl font-bold text-slate-800">{{ kbStats.total_chunks || 0 }}</p>
        </div>
        <div class="bg-white rounded-lg border border-slate-200 p-4">
          <p class="text-xs text-slate-500">AI 模型</p>
          <p class="mt-1 text-sm font-bold truncate" :class="ai.llmConfigured ? 'text-emerald-600' : 'text-amber-600'">
            {{ ai.llmConfigured ? ai.llmModel : '未配置（演示模式）' }}
          </p>
          <p class="text-[11px] text-slate-400 truncate mt-0.5">
            嵌入检索：{{ ai.embeddingAvailable ? ai.embeddingModel : 'BM25+重排兜底' }}
          </p>
        </div>
      </div>

      <!-- 项目列表 -->
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-base font-bold text-slate-800">我的标书项目</h2>
        <el-button type="primary" @click="createVisible = true">
          <el-icon class="mr-1"><Plus /></el-icon>新建标书项目
        </el-button>
      </div>

      <div v-if="loading" class="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <div v-for="i in 3" :key="i" class="h-40 rounded-lg bg-slate-200 animate-pulse" />
      </div>

      <EmptyState
        v-else-if="!projects.length"
        icon="Files"
        title="还没有标书项目"
        description="新建项目后，向导将引导你完成：上传招标文件 → 18项拆标 → 大纲规划 → 全局事实 → 开始撰写"
      >
        <el-button type="primary" @click="createVisible = true">创建第一个项目</el-button>
      </EmptyState>

      <div v-else class="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <div
          v-for="p in projects"
          :key="p.id"
          class="bg-white rounded-lg border border-slate-200 p-4 hover:shadow-md hover:border-blue-300 transition cursor-pointer group"
          @click="enterProject(p)"
        >
          <div class="flex items-start justify-between gap-2">
            <h3 class="font-bold text-slate-800 text-sm leading-snug line-clamp-2">{{ p.name }}</h3>
            <el-progress type="circle" :percentage="Math.round(p.completion_rate)" :width="44" :stroke-width="6" />
          </div>
          <p class="mt-1.5 text-xs text-slate-500">招标方：{{ p.client_name || '未填写' }}</p>
          <div class="mt-3 flex items-center justify-between">
            <el-tag size="small" :type="p.stage === 'writing' ? 'success' : p.stage === 'outline_confirmed' ? 'primary' : 'info'" effect="light">
              {{ STAGE_LABEL[p.stage] || p.stage }}
            </el-tag>
            <span class="text-[11px] text-slate-400">{{ p.updated_at }}</span>
          </div>
          <div class="mt-3 flex gap-2 opacity-0 group-hover:opacity-100 transition">
            <el-button size="small" type="primary" plain class="flex-1">
              {{ p.stage === 'writing' || p.stage === 'outline_confirmed' ? '进入编纂' : '继续向导' }}
            </el-button>
            <el-button size="small" text type="danger" @click.stop="removeProject(p)">删除</el-button>
          </div>
        </div>
      </div>
    </div>

    <!-- 新建项目弹窗 -->
    <el-dialog v-model="createVisible" title="新建标书项目" width="92%" class="!max-w-lg">
      <el-form label-position="top">
        <el-form-item label="项目名称（标书全称）" required>
          <el-input v-model="form.name" placeholder="如：智慧园区一体化管理平台建设项目" maxlength="80" />
        </el-form-item>
        <el-form-item label="招标方/客户名称">
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
