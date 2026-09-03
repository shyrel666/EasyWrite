<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import AppShell from '@/components/layout/AppShell.vue'

const templates = ref([])
const dialogVisible = ref(false)
const form = reactive({
  id: '', name: '自定义标书模板', primary_font: '宋体', heading_font: '黑体',
  theme_color: '#003366', header_text: '技术投标文件',
  margin_top: 2.54, margin_bottom: 2.54, margin_left: 3.0, margin_right: 2.54,
})

async function load() {
  const res = await api.templates()
  templates.value = res.templates
}

function themeHex(id) {
  const t = templates.value.find((t) => t.id === id)
  return t?.theme_rgb ? `rgb(${t.theme_rgb.join(',')})` : '#003366'
}

function openCreate() {
  form.id = `tpl_${Date.now().toString(36)}`
  dialogVisible.value = true
}

async function save() {
  try {
    await api.createTemplate({ ...form, theme_rgb: hexToRgb(form.theme_color) })
    ElMessage.success('模板已保存')
    dialogVisible.value = false
    load()
  } catch (e) {
    ElMessage.error('保存失败：' + e.message)
  }
}

function hexToRgb(hex) {
  const h = hex.replace('#', '')
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]
}

onMounted(load)
</script>

<template>
  <AppShell active="templates">
    <div class="max-w-6xl mx-auto px-3 sm:px-6 py-5">
      <div class="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <h2 class="font-bold text-slate-800">Word 排版模板中心</h2>
          <p class="text-xs text-slate-500 mt-0.5">导出时选择模板，支持政企科技蓝/党政红/商务简约等样式</p>
        </div>
        <el-button type="primary" @click="openCreate"><el-icon class="mr-1"><Plus /></el-icon>新增自定义模板</el-button>
      </div>

      <div class="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <div
          v-for="t in templates"
          :key="t.id"
          class="bg-white rounded-lg border border-slate-200 overflow-hidden hover:shadow-md transition cursor-default"
        >
          <div class="h-24 flex items-end p-4" :style="{ background: `linear-gradient(135deg, ${themeHex(t.id)}, #1e293b)` }">
            <div>
              <p class="text-white font-bold text-sm">{{ t.name }}</p>
              <p class="text-white/70 text-xs mt-0.5">正文 {{ t.primary_font }} · 标题 {{ t.heading_font }}</p>
            </div>
          </div>
          <div class="p-3.5">
            <p class="text-[11px] text-slate-500 leading-relaxed">
              页边距 上{{ t.margin_top }}cm 下{{ t.margin_bottom }}cm · 页眉「{{ t.header_text }}」<br />
              导出含封面 / 目录域 / 页码（第X页 共Y页）
            </p>
          </div>
        </div>
      </div>

      <el-dialog v-model="dialogVisible" title="新增自定义模板" width="92%" class="!max-w-lg">
        <el-form label-position="top">
          <div class="grid grid-cols-2 gap-x-3">
            <el-form-item label="模板名称"><el-input v-model="form.name" /></el-form-item>
            <el-form-item label="页眉文字"><el-input v-model="form.header_text" /></el-form-item>
            <el-form-item label="正文字体"><el-input v-model="form.primary_font" placeholder="宋体" /></el-form-item>
            <el-form-item label="标题字体"><el-input v-model="form.heading_font" placeholder="黑体" /></el-form-item>
            <el-form-item label="主题色"><el-color-picker v-model="form.theme_color" class="!w-full" /></el-form-item>
            <el-form-item label="页边距（cm）">
              <div class="flex gap-1">
                <el-input-number v-model="form.margin_top" :step="0.1" size="small" class="!w-16" />
                <el-input-number v-model="form.margin_bottom" :step="0.1" size="small" class="!w-16" />
              </div>
            </el-form-item>
          </div>
        </el-form>
        <template #footer>
          <el-button @click="dialogVisible = false">取消</el-button>
          <el-button type="primary" @click="save">保存模板</el-button>
        </template>
      </el-dialog>
    </div>
  </AppShell>
</template>
