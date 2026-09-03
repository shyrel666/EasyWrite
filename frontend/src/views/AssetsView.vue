<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import AppShell from '@/components/layout/AppShell.vue'
import EmptyState from '@/components/common/EmptyState.vue'

const TYPES = [
  { key: 'qualifications', label: '资质认证', fields: [
    { k: 'name', l: '资质证书全称', required: true },
    { k: 'cert_no', l: '证书编号', required: true },
    { k: 'category', l: '分类' },
    { k: 'level', l: '等级' },
    { k: 'issue_org', l: '发证机构', required: true },
    { k: 'issue_date', l: '发证日期' },
    { k: 'expiry_date', l: '有效期截止' },
    { k: 'summary', l: '投标适用说明', area: true },
  ]},
  { key: 'personnel', label: '核心人员', fields: [
    { k: 'name', l: '姓名', required: true },
    { k: 'role', l: '拟任岗位', required: true },
    { k: 'years_of_experience', l: '从业年限' },
    { k: 'education', l: '学历院校' },
    { k: 'professional_title', l: '职称' },
    { k: 'certificates', l: '持证清单（用、分隔）' },
    { k: 'intro', l: '能力述评', area: true },
  ]},
  { key: 'cases', label: '中标业绩', fields: [
    { k: 'project_name', l: '业绩项目全称', required: true },
    { k: 'client_name', l: '客户单位', required: true },
    { k: 'contract_amount', l: '合同金额' },
    { k: 'sign_date', l: '签约时间' },
    { k: 'contract_category', l: '业务领域' },
    { k: 'key_deliverables', l: '核心交付（用、分隔）' },
    { k: 'acceptance_status', l: '验收结论' },
    { k: 'summary', l: '成效总结', area: true },
  ]},
  { key: 'components', label: '方案组件', fields: [
    { k: 'name', l: '组件名称', required: true },
    { k: 'category', l: '分类' },
    { k: 'tags', l: '技术标签（用、分隔）' },
    { k: 'summary', l: '设计概述', area: true },
    { k: 'content', l: '标准方案正文', area: true, required: true },
  ]},
]

const activeType = ref('qualifications')
const list = ref([])
const stats = ref({})
const dialogVisible = ref(false)
const editing = ref(null)

const blankForm = () => {
  const form = { id: '' }
  const t = TYPES.find((t) => t.key === activeType.value)
  for (const f of t.fields) form[f.k] = f.area ? '' : ''
  if (activeType.value === 'personnel') {
    form.years_of_experience = 8
    form.education = '大学本科'
    form.professional_title = '高级工程师'
  }
  return form
}

const form = reactive(blankForm())

async function load() {
  const res = await api.assets.list(activeType.value)
  list.value = res
  stats.value = await api.assets.stats()
}

function openAdd() {
  Object.assign(form, blankForm())
  editing.value = null
  dialogVisible.value = true
}

function openEdit(item) {
  Object.assign(form, blankForm(), JSON.parse(JSON.stringify(item)))
  editing.value = item.id
  dialogVisible.value = true
}

async function save() {
  const t = TYPES.find((t) => t.key === activeType.value)
  for (const f of t.fields) {
    if (f.required && !String(form[f.k] || '').trim()) {
      ElMessage.warning(`请填写：${f.l}`)
      return
    }
  }
  const payload = { ...form }
  if (payload.id === '') payload.id = `asset_${Date.now().toString(36)}`
  for (const f of t.fields) {
    if (['certificates', 'key_deliverables', 'tags'].includes(f.k)) {
      payload[f.k] = String(payload[f.k] || '').split(/[、,]/).map((s) => s.trim()).filter(Boolean)
    }
  }
  if (activeType.value === 'personnel') payload.years_of_experience = Number(payload.years_of_experience) || 0
  try {
    await api.assets.add(activeType.value, payload)
    ElMessage.success(editing.value ? '已更新' : '已新增')
    dialogVisible.value = false
    load()
  } catch (e) {
    ElMessage.error('保存失败：' + e.message)
  }
}

async function remove(item) {
  try {
    await api.assets.remove(activeType.value, item.id)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    ElMessage.error('删除失败：' + e.message)
  }
}

function cardTitle(item) {
  return item.name || item.project_name || item.id
}
function cardSub(item) {
  const t = TYPES.find((t) => t.key === activeType.value)
  if (activeType.value === 'qualifications') return `${item.level || ''} · ${item.issue_org}`
  if (activeType.value === 'personnel') return `${item.role} · ${item.years_of_experience}年经验`
  if (activeType.value === 'cases') return `${item.client_name} · ${item.contract_amount}`
  return item.category || item.tags?.join(' / ') || ''
}

onMounted(load)
</script>

<template>
  <AppShell active="assets">
    <div class="max-w-6xl mx-auto px-3 sm:px-6 py-5">
      <div class="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <h2 class="font-bold text-slate-800">企业资产中台</h2>
          <p class="text-xs text-slate-500 mt-0.5">
            资质/人员/业绩/组件在章节撰写时自动匹配注入；组件正文直接作为可复用方案资产。
          </p>
        </div>
        <el-button type="primary" @click="openAdd"><el-icon class="mr-1"><Plus /></el-icon>录入新资产</el-button>
      </div>

      <el-tabs v-model="activeType" @tab-change="load">
        <el-tab-pane v-for="t in TYPES" :key="t.key" :label="t.label" :name="t.key" />
      </el-tabs>

      <EmptyState v-if="!list.length" icon="Box" title="暂无资产" description="录入后在标书生成时自动匹配引用" />

      <div v-else class="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
        <div v-for="item in list" :key="item.id" class="bg-white rounded-lg border border-slate-200 p-4 hover:shadow-sm transition">
          <div class="flex items-start justify-between gap-2">
            <p class="font-medium text-slate-800 text-sm leading-snug">{{ cardTitle(item) }}</p>
            <div class="flex gap-0.5 shrink-0">
              <el-button size="small" text @click="openEdit(item)"><el-icon><Edit /></el-icon></el-button>
              <el-button size="small" text type="danger" @click="remove(item)"><el-icon><Delete /></el-icon></el-button>
            </div>
          </div>
          <p class="text-xs text-slate-500 mt-1">{{ cardSub(item) }}</p>
          <p v-if="item.summary || item.intro" class="text-xs text-slate-400 mt-2 line-clamp-2">{{ item.summary || item.intro }}</p>
        </div>
      </div>

      <el-dialog v-model="dialogVisible" :title="editing ? '编辑资产' : '录入新资产'" width="92%" class="!max-w-xl">
        <el-form label-position="top">
          <div class="grid sm:grid-cols-2 gap-x-3">
            <el-form-item v-for="f in TYPES.find((t) => t.key === activeType).fields" :key="f.k" :label="f.l" :class="{ 'sm:col-span-2': f.area }">
              <el-input
                v-if="!f.area"
                v-model="form[f.k]"
                :placeholder="`填写${f.l}`"
              />
              <el-input v-else v-model="form[f.k]" type="textarea" :rows="3" :placeholder="`填写${f.l}`" />
            </el-form-item>
          </div>
        </el-form>
        <template #footer>
          <el-button @click="dialogVisible = false">取消</el-button>
          <el-button type="primary" @click="save">保存</el-button>
        </template>
      </el-dialog>
    </div>
  </AppShell>
</template>
