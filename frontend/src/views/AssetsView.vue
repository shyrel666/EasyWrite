<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import AppShell from '@/components/layout/AppShell.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import PageHeader from '@/components/common/PageHeader.vue'

const TYPES = [
  { key: 'qualifications', label: '资质认证', icon: 'Medal', fields: [
    { k: 'name', l: '资质证书全称', required: true },
    { k: 'cert_no', l: '证书编号', required: true },
    { k: 'category', l: '分类' },
    { k: 'level', l: '等级' },
    { k: 'issue_org', l: '发证机构', required: true },
    { k: 'issue_date', l: '发证日期' },
    { k: 'expiry_date', l: '有效期截止' },
    { k: 'summary', l: '投标适用说明', area: true },
  ]},
  { key: 'personnel', label: '核心人员', icon: 'User', fields: [
    { k: 'name', l: '姓名', required: true },
    { k: 'role', l: '拟任岗位', required: true },
    { k: 'years_of_experience', l: '从业年限' },
    { k: 'education', l: '学历院校' },
    { k: 'professional_title', l: '职称' },
    { k: 'certificates', l: '持证清单（用、分隔）' },
    { k: 'intro', l: '能力述评', area: true },
  ]},
  { key: 'cases', label: '中标业绩', icon: 'Trophy', fields: [
    { k: 'project_name', l: '业绩项目全称', required: true },
    { k: 'client_name', l: '客户单位', required: true },
    { k: 'contract_amount', l: '合同金额' },
    { k: 'sign_date', l: '签约时间' },
    { k: 'contract_category', l: '业务领域' },
    { k: 'key_deliverables', l: '核心交付（用、分隔）' },
    { k: 'acceptance_status', l: '验收结论' },
    { k: 'summary', l: '成效总结', area: true },
  ]},
  { key: 'components', label: '方案组件', icon: 'Grid', fields: [
    { k: 'name', l: '组件名称', required: true },
    { k: 'category', l: '分类' },
    { k: 'tags', l: '技术标签（用、分隔）' },
    { k: 'summary', l: '设计概述', area: true },
    { k: 'content', l: '标准方案正文', area: true, required: true },
  ]},
]

const activeType = ref('qualifications')
const currentType = computed(() => TYPES.find((t) => t.key === activeType.value))

function switchType(key) {
  if (activeType.value === key) return
  activeType.value = key
  list.value = []
  load()
}
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
    await ElMessageBox.confirm(`删除「${cardTitle(item)}」？`, '删除资产', { type: 'warning' })
  } catch { return }
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

function cardTags(item) {
  const tags = activeType.value === 'personnel' ? item.certificates
    : activeType.value === 'cases' ? item.key_deliverables
      : activeType.value === 'components' ? item.tags
        : [item.cert_no, item.expiry_date && `有效期至 ${item.expiry_date}`]
  return (Array.isArray(tags) ? tags : []).filter(Boolean)
}

onMounted(load)
</script>

<template>
  <AppShell active="assets">
    <div class="max-w-6xl mx-auto px-4 sm:px-8 py-6 sm:py-10">
      <PageHeader
        eyebrow="企业资产"
        title="企业资产中台"
        description="资质、人员、业绩与方案组件在章节撰写时自动匹配注入；组件正文可直接作为可复用的方案段落。"
      >
        <template #actions>
          <el-button type="primary" @click="openAdd"><el-icon class="mr-1.5"><Plus /></el-icon>录入{{ currentType.label }}</el-button>
        </template>
      </PageHeader>

      <div class="seg mb-5 overflow-x-auto max-w-full">
        <button
          v-for="t in TYPES"
          :key="t.key"
          class="seg-item !px-3.5 !py-1.5 flex items-center gap-1.5"
          :class="{ 'is-active': activeType === t.key }"
          @click="switchType(t.key)"
        >
          <el-icon><component :is="t.icon" /></el-icon>{{ t.label }}
          <span class="num text-ink-3">{{ stats[`total_${t.key}`] ?? '' }}</span>
        </button>
      </div>

      <EmptyState v-if="!list.length" :icon="currentType.icon" :title="`暂无${currentType.label}`" description="录入后在标书生成时自动匹配引用。">
        <el-button @click="openAdd">录入第一条</el-button>
      </EmptyState>

      <div v-else class="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <article
          v-for="(item, i) in list"
          :key="item.id"
          class="card group p-5 flex flex-col hover:border-line-strong hover:shadow-sheet transition rise"
          :style="{ animationDelay: `${Math.min(i, 8) * 30}ms` }"
        >
          <div class="flex items-start gap-3">
            <span
              v-if="activeType === 'personnel'"
              class="w-10 h-10 rounded-full bg-accent-soft text-accent-fg font-kai text-lg flex items-center justify-center shrink-0"
            >{{ (item.name || '?').slice(0, 1) }}</span>
            <span v-else class="w-10 h-10 rounded-xl bg-sunken text-ink-2 flex items-center justify-center shrink-0">
              <el-icon :size="18"><component :is="currentType.icon" /></el-icon>
            </span>
            <div class="min-w-0 flex-1">
              <p class="text-sm font-semibold text-ink leading-snug line-clamp-2">{{ cardTitle(item) }}</p>
              <p class="text-xs text-ink-2 mt-1 line-clamp-1">{{ cardSub(item) }}</p>
            </div>
            <div class="flex shrink-0 -mr-1.5 -mt-1 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition">
              <button class="icon-btn !w-7 !h-7" title="编辑" @click="openEdit(item)"><el-icon><Edit /></el-icon></button>
              <button class="icon-btn !w-7 !h-7 hover:!text-bad" title="删除" @click="remove(item)"><el-icon><Delete /></el-icon></button>
            </div>
          </div>
          <p v-if="item.summary || item.intro" class="text-xs text-ink-3 mt-3 line-clamp-3 leading-relaxed">{{ item.summary || item.intro }}</p>
          <div v-if="cardTags(item).length" class="flex flex-wrap gap-1.5 mt-auto pt-4">
            <span v-for="tag in cardTags(item).slice(0, 4)" :key="tag" class="chip chip-mute max-w-full truncate">{{ tag }}</span>
            <span v-if="cardTags(item).length > 4" class="chip chip-mute num">+{{ cardTags(item).length - 4 }}</span>
          </div>
        </article>
      </div>

      <el-dialog v-model="dialogVisible" :title="`${editing ? '编辑' : '录入'}${currentType.label}`" width="min(620px, 94vw)">
        <el-form label-position="top" @submit.prevent>
          <div class="grid sm:grid-cols-2 gap-x-4">
            <el-form-item
              v-for="f in currentType.fields"
              :key="f.k"
              :label="f.l"
              :required="f.required"
              :class="{ 'sm:col-span-2': f.area }"
            >
              <el-input v-if="!f.area" v-model="form[f.k]" :placeholder="`填写${f.l}`" />
              <el-input v-else v-model="form[f.k]" type="textarea" :rows="f.k === 'content' ? 6 : 3" :placeholder="`填写${f.l}`" />
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
