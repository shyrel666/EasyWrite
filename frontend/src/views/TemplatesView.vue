<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import AppShell from '@/components/layout/AppShell.vue'
import PageHeader from '@/components/common/PageHeader.vue'

const templates = ref([])
const dialogVisible = ref(false)
const form = reactive({
  id: '', name: '自定义标书模板', primary_font: '宋体', heading_font: '黑体',
  theme_color: '#003366', header_text: '技术投标文件',
  margin_top: 2.54, margin_bottom: 2.54, margin_left: 3.0, margin_right: 2.54,
})

// 导出对话框记住的上次选择（仅本浏览器）
let lastUsed = ''
try {
  lastUsed = localStorage.getItem('easywrite.exportTemplate') || ''
} catch { /* 存储不可用 */ }

async function load() {
  const res = await api.templates()
  templates.value = res.templates
}

function themeColor(t) {
  return t.theme_rgb ? `rgb(${t.theme_rgb.join(',')})` : (t.theme_color || '#003366')
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
    <div class="max-w-6xl mx-auto px-4 sm:px-8 py-6 sm:py-10">
      <PageHeader
        eyebrow="排版模板"
        title="Word 排版模板"
        description="导出标书时选择模板：正文与标题字体、主题色、页边距与页眉。导出文档均含封面、目录域与“第 X 页 共 Y 页”页码。"
      >
        <template #actions>
          <el-button type="primary" @click="openCreate"><el-icon class="mr-1.5"><Plus /></el-icon>新增模板</el-button>
        </template>
      </PageHeader>

      <div class="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
        <article
          v-for="(t, i) in templates"
          :key="t.id"
          class="card overflow-hidden group hover:shadow-sheet hover:border-line-strong transition rise"
          :style="{ animationDelay: `${Math.min(i, 8) * 40}ms` }"
        >
          <!-- 纸面缩略：按真实尺寸排版后缩小一半 -->
          <div class="h-52 doc-desk relative overflow-hidden border-b border-line">
            <div
              class="absolute left-1/2 top-7 w-[300px] h-[400px] origin-top -translate-x-1/2 scale-[0.5] group-hover:top-5 transition-all duration-300"
            >
              <div class="w-full h-full bg-[#fffefb] rounded-[3px] shadow-sheet px-9 pt-6 text-[#222]" :style="{ fontFamily: t.primary_font }">
                <div class="flex justify-end text-[11px] pb-1.5 border-b-[1.5px]" :style="{ borderColor: themeColor(t), color: themeColor(t) }">
                  {{ t.header_text }}
                </div>
                <p class="mt-7 text-[19px] font-bold" :style="{ fontFamily: t.heading_font, color: themeColor(t) }">第一章 总体技术方案</p>
                <p class="mt-3 text-[14px] font-bold" :style="{ fontFamily: t.heading_font }">1.1 建设目标</p>
                <p class="mt-2 text-[12px] leading-[1.9] indent-[2em]">
                  本项目以统一平台、分级响应、闭环改进为总体思路，围绕可用性、安全性与服务质量组织实施，确保全年核心业务稳定运行。
                </p>
                <p class="mt-1 text-[12px] leading-[1.9] indent-[2em]">运维团队按三级响应机制组织，各级职责与升级时限明确。</p>
              </div>
            </div>
            <span v-if="t.id === lastUsed" class="chip chip-accent absolute top-3 left-3">上次导出使用</span>
          </div>
          <div class="p-4 sm:p-5">
            <div class="flex items-center gap-2">
              <span class="w-3 h-3 rounded-full shrink-0 ring-2 ring-surface" :style="{ background: themeColor(t) }" />
              <p class="text-sm font-semibold text-ink truncate">{{ t.name }}</p>
            </div>
            <p v-if="t.description" class="text-xs text-ink-2 mt-1.5 leading-relaxed line-clamp-2">{{ t.description }}</p>
            <div class="mt-3 flex flex-wrap gap-1.5">
              <span class="chip chip-mute">正文 {{ t.primary_font }}</span>
              <span class="chip chip-mute">标题 {{ t.heading_font }}</span>
              <span class="chip chip-mute num">边距 {{ t.margin_top }} / {{ t.margin_bottom }} / {{ t.margin_left }} / {{ t.margin_right }} cm</span>
            </div>
          </div>
        </article>
      </div>

      <el-dialog v-model="dialogVisible" title="新增排版模板" width="min(560px, 94vw)">
        <el-form label-position="top" @submit.prevent>
          <div class="grid grid-cols-2 gap-x-4">
            <el-form-item label="模板名称"><el-input v-model="form.name" /></el-form-item>
            <el-form-item label="页眉文字"><el-input v-model="form.header_text" /></el-form-item>
            <el-form-item label="正文字体"><el-input v-model="form.primary_font" placeholder="宋体" /></el-form-item>
            <el-form-item label="标题字体"><el-input v-model="form.heading_font" placeholder="黑体" /></el-form-item>
            <el-form-item label="主题色" class="col-span-2">
              <div class="flex items-center gap-3">
                <el-color-picker v-model="form.theme_color" />
                <span class="text-xs text-ink-3 num">{{ form.theme_color }}</span>
              </div>
            </el-form-item>
          </div>
          <el-form-item label="页边距（cm）：上 / 下 / 左 / 右">
            <div class="grid grid-cols-4 gap-2 w-full">
              <el-input-number v-model="form.margin_top" :step="0.1" :min="0" :controls="false" class="!w-full" />
              <el-input-number v-model="form.margin_bottom" :step="0.1" :min="0" :controls="false" class="!w-full" />
              <el-input-number v-model="form.margin_left" :step="0.1" :min="0" :controls="false" class="!w-full" />
              <el-input-number v-model="form.margin_right" :step="0.1" :min="0" :controls="false" class="!w-full" />
            </div>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="dialogVisible = false">取消</el-button>
          <el-button type="primary" @click="save">保存模板</el-button>
        </template>
      </el-dialog>
    </div>
  </AppShell>
</template>
