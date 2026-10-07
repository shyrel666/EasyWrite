<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useProjectStore } from '@/stores/project'
import { useAiStore } from '@/stores/ai'
import AppShell from '@/components/layout/AppShell.vue'
import TaskProgress from '@/components/common/TaskProgress.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { isActiveTask } from '@/utils/tasks'

const route = useRoute()
const router = useRouter()
const store = useProjectStore()
const ai = useAiStore()

const projectId = route.params.id
const step = ref(0)
const busy = ref(false)

const STEPS = [
  { title: '上传招标文件', desc: '18 项结构化拆标', heading: '上传招标文件', intro: '系统将自动拆解 18 项核心要素：评分办法、★号红线条款、资质门槛、工期与预算等。' },
  { title: '确认拆标结果', desc: '评分细则与红线', heading: '确认拆标结果', intro: '逐项核对抽取结果，特别是评分细则的分值合计与废标红线条款。修改后点「确认」应用到项目。' },
  { title: '规划大纲', desc: '按评分项对齐', heading: '规划技术标大纲', intro: '先确认一级章节结构，再一键展开完整章节树，字数预算按评分比重自动分配。' },
  { title: '全局事实', desc: '撰写硬约束', heading: '设定全局事实', intro: '这些事实以只读硬约束贯穿全书撰写，避免公司名、产品名、数据库选型前后不一致。' },
]
const EXTRACTS = ['项目概况', '预算限价', '评分细则', '★号红线', '重要条款', '资质门槛', '工期质保', '付款节点']
const FACT_FIELDS = [
  { k: 'company_name', l: '投标企业法定全称' },
  { k: 'credit_code', l: '统一社会信用代码' },
  { k: 'legal_rep', l: '法定代表人' },
  { k: 'registered_capital', l: '注册资本' },
  { k: 'core_product_name', l: '核心产品 / 平台名称' },
  { k: 'architecture_stack', l: '统一技术架构路线' },
  { k: 'database_selection', l: '数据库底座选型' },
  { k: 'sla_commitment', l: 'SLA 售后承诺' },
  { k: 'delivery_guarantee', l: '工期交付承诺' },
]

// ---------- Step 0: 上传招标文件 ----------
const fileInput = ref(null)
const uploading = ref(false)
const analyzeTaskId = ref('')
const analyzeTaskDone = ref(false)
const analyzeTaskFailed = ref(false)
const analyzeInterrupted = ref(null)
const pasteMode = ref(false)
const dragOver = ref(false)
const pasteText = ref('')
const analysis = ref(null)

// ---------- Step 1: 拆标确认 ----------
const editableAnalysis = ref(null)

// ---------- Step 2: 大纲规划 ----------
const chapters = ref([])
const drafting = ref(false)
const expanding = ref(false)
const outlineMode = ref('')
const outlineMessage = ref('')
const totalBudget = ref(30000)

// ---------- Step 3: 全局事实 ----------
const facts = ref({
  company_name: '', credit_code: '', legal_rep: '', registered_capital: '',
  core_product_name: '', architecture_stack: '', database_selection: '',
  sla_commitment: '', delivery_guarantee: '', fact_completeness_mode: 'placeholder',
  custom_facts: {},
})

const FIELD_LABELS = {
  project_name: '项目名称', tender_number: '招标编号', purchaser_name: '采购人',
  budget_limit: '预算/最高限价', duration_requirement: '工期要求', warranty_period: '质保期',
  bid_security: '投标保证金', bid_validity_period: '投标有效期', scoring_method: '评标办法',
  price_score_weight: '价格分', tech_score_weight: '技术分', business_score_weight: '商务分',
  project_location: '实施地点', payment_milestones: '付款节点', site_survey_rules: '现场踏勘',
  submission_deadline: '投标截止时间',
}

const simpleFields = computed(() => Object.keys(FIELD_LABELS))

// ---------- 评分细则 ----------
const RESPONSE_TYPES = {
  proposal: { label: '撰写方案', type: 'primary' },
  evidence: { label: '证明材料', type: 'success' },
  demo: { label: '现场演示', type: 'warning' },
  compliance: { label: '逐条响应', type: 'danger' },
  price: { label: '报价', type: 'info' },
}
const scoringItems = computed(() => editableAnalysis.value?.scoring_items || [])
const scoringPackages = computed(() => [...new Set(scoringItems.value.map((it) => it.package))])
const packageItems = computed(() =>
  scoringItems.value.filter((it) => it.package === (editableAnalysis.value?.target_package || ''))
)
const packageTotal = computed(() => packageItems.value.reduce((sum, it) => sum + (Number(it.points) || 0), 0))
// 需在技术标中承接的评分项（报价除外）及尚未被任何章节承接的评分项
const chapterItems = computed(() => packageItems.value.filter((it) => it.response_type !== 'price'))
const uncoveredItems = computed(() => {
  const covered = new Set(chapters.value.flatMap((c) => c.scoring_item_ids || []))
  return chapterItems.value.filter((it) => !covered.has(it.id))
})

async function handleFileUpload(event) {
  const file = event.target.files[0]
  if (!file) return
  if (!/\.(docx|pdf)$/i.test(file.name)) {
    ElMessage.warning('仅支持 .docx / .pdf 格式招标文件（.doc 请先另存为 .docx）')
    event.target.value = ''
    return
  }
  uploading.value = true
  analyzeTaskDone.value = false
  analyzeTaskFailed.value = false
  analyzeInterrupted.value = null
  try {
    const res = await api.analyzeTender(file, projectId)
    analyzeTaskId.value = res.task_id
  } catch (e) {
    ElMessage.error('上传失败：' + e.message)
    uploading.value = false
  }
}

// 拖拽上传与点击选择走同一套校验
function onDrop(event) {
  dragOver.value = false
  const files = event.dataTransfer?.files
  if (files?.length) handleFileUpload({ target: { files, value: '' } })
}

async function runPasteAnalysis() {
  if (pasteText.value.trim().length < 50) {
    ElMessage.warning('粘贴的招标文件正文过短，请确认内容完整')
    return
  }
  uploading.value = true
  try {
    analysis.value = await api.analyzeTenderText(pasteText.value)
    editableAnalysis.value = { ...analysis.value }
    applyAnalysis()
  } catch (e) {
    ElMessage.error('拆标失败：' + e.message)
  } finally {
    uploading.value = false
  }
}

function onAnalyzeDone(task) {
  uploading.value = false
  if (task.status !== 'completed' || !task.result) {
    analyzeTaskFailed.value = true
    return
  }
  analyzeTaskDone.value = true
  analysis.value = task.result
  editableAnalysis.value = { ...task.result }
  if (task.result.extraction_mode === 'rules') {
    ElMessage.warning('当前为正则规则抽取（覆盖度有限）。配置大模型后可获得完整 18 项拆解。')
  }
}

function onAnalyzeFailed(task) {
  uploading.value = false
  analyzeTaskFailed.value = true
  ElMessage.error('拆标失败：' + (task.error || '').split('\n')[0])
}

// 重新选择文件：回到上传区
function resetUpload() {
  analyzeTaskId.value = ''
  analyzeTaskDone.value = false
  analyzeTaskFailed.value = false
  if (fileInput.value) fileInput.value.value = ''
}

function applyAnalysis() {
  const payload = { ...editableAnalysis.value }
  api.applyTender(projectId, payload, pasteText.value).then((res) => {
    store.project.stage = res.stage
    ElMessage.success(`拆标已应用：提取 ${res.star_count} 条★号红线条款`)
  }).catch((e) => ElMessage.error('应用失败：' + e.message))
}

async function draftChapters() {
  drafting.value = true
  try {
    const res = await api.draftLevel1(projectId)
    chapters.value = res.chapters.map((c) => ({
      ...c,
      reqText: (c.requirements || []).join('；'),
    }))
    outlineMode.value = res.mode
    outlineMessage.value = res.message
    if (res.mode !== 'llm') ElMessage.warning(res.message || 'AI 大纲生成不可用，已载入标准结构')
  } catch (e) {
    ElMessage.error('大纲生成失败：' + e.message)
  } finally {
    drafting.value = false
  }
}

async function expandOutline() {
  if (!chapters.value.length) {
    ElMessage.warning('请先生成或编辑一级章节')
    return
  }
  const cleaned = chapters.value.map((c) => ({
    title: c.title.trim(),
    requirements: (c.reqText || '')
      .split(/[；;]/)
      .map((s) => s.trim())
      .filter(Boolean),
    scoring_item_ids: c.scoring_item_ids || [],
  }))
  expanding.value = true
  try {
    const res = await api.expandOutline(projectId, cleaned, totalBudget.value)
    store.project.outline = res.outline
    store.project.stage = res.stage
    ElMessage.success(`大纲展开完成：共 ${countNodes(res.outline)} 个章节节点`)
    step.value = 3
  } catch (e) {
    ElMessage.error('大纲展开失败：' + e.message)
  } finally {
    expanding.value = false
  }
}

function countNodes(nodes) {
  return nodes.reduce((acc, n) => acc + 1 + (n.children ? countNodes(n.children) : 0), 0)
}

function addChapter() {
  chapters.value.push({ title: '', requirements: [], reqText: '', scoring_item_ids: [] })
}
function removeChapter(i) {
  chapters.value.splice(i, 1)
}

async function saveFactsAndFinish() {
  busy.value = true
  try {
    await store.saveFacts(facts.value)
    await api.updateStage(projectId, 'writing')
    ElMessage.success('全局事实已保存，进入编纂台')
    router.push(`/project/${projectId}/workspace`)
  } catch (e) {
    ElMessage.error('保存失败：' + e.message)
  } finally {
    busy.value = false
  }
}

async function skipToWorkspace() {
  busy.value = true
  try {
    await store.saveFacts(facts.value)
    await api.updateStage(projectId, 'writing')
    router.push(`/project/${projectId}/workspace`)
  } catch (e) {
    ElMessage.error('保存失败：' + e.message)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  const p = await store.load(projectId)
  facts.value = { ...facts.value, ...(p.facts || {}), custom_facts: p.facts?.custom_facts || {} }
  if (p.tender_analysis) {
    analysis.value = { ...p.tender_analysis }
    editableAnalysis.value = { ...p.tender_analysis }
  }
  if (p.outline && p.outline.length) {
    chapters.value = p.outline.map((n) => ({
      title: n.title,
      requirements: n.requirements || [],
      reqText: (n.requirements || []).join('；'),
      scoring_item_ids: n.scoring_item_ids || [],
    }))
  }
  // 初始步骤：有正文进入编纂；有大纲进入大纲步；否则从头开始
  if (p.outline?.length) step.value = 3
  else if (p.tender_analysis) step.value = 1
  else await restoreAnalyzeTask()
})

// 尚未确认拆标结果时：接管进行中的拆标、找回已完成但未应用的结果，或提示上次因重启中断
async function restoreAnalyzeTask() {
  try {
    const last = await api.latestTask(projectId, 'tender_analyze')
    if (isActiveTask(last)) {
      analyzeTaskId.value = last.id
      uploading.value = true
    } else if (last?.status === 'completed' && last.result) {
      analyzeTaskId.value = last.id
    } else if (last?.status === 'interrupted') {
      analyzeInterrupted.value = last
    }
  } catch { /* 忽略 */ }
}
</script>

<template>
  <AppShell :project-id="projectId" :project-name="store.project?.name" active="wizard">
    <div class="max-w-6xl mx-auto px-4 sm:px-8 py-6 sm:py-10">
      <div class="grid lg:grid-cols-[208px_minmax(0,1fr)] gap-6 lg:gap-12">
        <!-- ===== 步骤 ===== -->
        <nav class="lg:sticky lg:top-10 self-start">
          <p class="eyebrow mb-3 hidden lg:block">项目向导</p>
          <ol class="flex lg:flex-col gap-2 lg:gap-0">
            <li v-for="(s, i) in STEPS" :key="s.title" class="relative lg:flex-none" :class="i === step ? 'flex-1' : ''">
              <span
                v-if="i < STEPS.length - 1"
                class="hidden lg:block absolute left-[17px] top-10 bottom-0 w-px"
                :class="i < step ? 'bg-ok/50' : 'bg-line'"
              />
              <button
                class="relative w-full flex items-center lg:items-start gap-3 rounded-lg p-1.5 lg:pb-5 text-left transition-colors"
                :class="i < step ? 'cursor-pointer group' : 'cursor-default'"
                :disabled="i >= step"
                @click="i < step && (step = i)"
              >
                <span
                  class="w-[26px] h-[26px] mt-0.5 shrink-0 rounded-full flex items-center justify-center text-xs font-semibold num transition"
                  :class="i < step ? 'bg-ok text-white group-hover:ring-4 group-hover:ring-ok/15' : i === step ? 'bg-accent text-white ring-4 ring-accent/15' : 'bg-surface border border-line-strong text-ink-3'"
                >
                  <el-icon v-if="i < step" :size="13"><Check /></el-icon>
                  <template v-else>{{ i + 1 }}</template>
                </span>
                <span class="min-w-0">
                  <span class="text-[13px] font-medium leading-5" :class="[i === step ? 'text-ink block' : i < step ? 'text-ink-2 group-hover:text-ink hidden lg:block' : 'text-ink-3 hidden lg:block']">{{ s.title }}</span>
                  <span class="hidden lg:block text-2xs text-ink-3 mt-0.5">{{ s.desc }}</span>
                </span>
              </button>
            </li>
          </ol>
        </nav>

        <div class="min-w-0">
          <PageHeader :eyebrow="`第 ${step + 1} 步 / 共 ${STEPS.length} 步`" :title="STEPS[step].heading" :description="STEPS[step].intro" />

          <!-- ============ Step 0：上传 ============ -->
          <section v-if="step === 0" class="space-y-4 rise">
            <div v-if="analyzeInterrupted && !analyzeTaskId" class="note note-warn">
              <el-icon class="text-warn mt-0.5 shrink-0"><WarningFilled /></el-icon>
              <span>上次的{{ analyzeInterrupted.title || '拆标分析' }}因服务重启中断，请重新上传。</span>
            </div>

            <template v-if="!analyzeTaskId && !pasteMode">
              <div
                class="relative rounded-2xl border-2 border-dashed px-6 py-14 sm:py-16 text-center cursor-pointer transition"
                :class="dragOver ? 'border-accent bg-accent-soft/60' : 'border-line-strong bg-surface hover:border-accent/60 hover:bg-accent-soft/30'"
                @click="fileInput.click()"
                @dragover.prevent="dragOver = true"
                @dragleave.prevent="dragOver = false"
                @drop.prevent="onDrop"
              >
                <div class="relative mx-auto w-16 h-20">
                  <span class="absolute inset-0 rounded-md bg-sunken border border-line rotate-[-8deg] -translate-x-2" />
                  <span class="absolute inset-0 rounded-md bg-surface border border-line-strong shadow-sheet flex flex-col gap-1.5 p-2.5 pt-3">
                    <span class="h-1 w-2/3 rounded bg-accent/70" />
                    <span v-for="n in 4" :key="n" class="h-[3px] rounded bg-line-strong" :class="n === 4 ? 'w-1/2' : 'w-full'" />
                  </span>
                  <span class="absolute -right-3 -bottom-2 w-8 h-8 rounded-full bg-accent text-white flex items-center justify-center shadow-float">
                    <el-icon :size="15"><Upload /></el-icon>
                  </span>
                </div>
                <p class="mt-7 text-[15px] font-semibold text-ink">将招标文件拖到这里，或点击选择</p>
                <p class="mt-1.5 text-xs text-ink-3">支持 .docx / .pdf · 扫描件 PDF 暂不支持文字识别，请使用可复制文字的版本</p>
                <input ref="fileInput" type="file" accept=".docx,.pdf" class="hidden" @change="handleFileUpload" />
              </div>
              <div class="flex items-center justify-between gap-3 flex-wrap">
                <div class="flex flex-wrap gap-1.5">
                  <span v-for="t in EXTRACTS" :key="t" class="chip chip-mute">{{ t }}</span>
                </div>
                <button class="text-xs font-medium text-accent-fg hover:underline underline-offset-2" @click="pasteMode = true">
                  没有文件？粘贴正文文本 →
                </button>
              </div>
            </template>

            <div v-else-if="pasteMode && !analyzeTaskId" class="card p-5 space-y-3">
              <el-input
                v-model="pasteText"
                type="textarea"
                :rows="14"
                placeholder="粘贴招标文件关键章节正文（投标须知、评分办法、技术要求部分越完整效果越好）"
              />
              <div class="flex gap-2 justify-end">
                <el-button @click="pasteMode = false">返回上传</el-button>
                <el-button type="primary" :loading="uploading" @click="runPasteAnalysis">执行 18 项拆标</el-button>
              </div>
            </div>

            <div v-else class="card p-5 sm:p-6 space-y-4">
              <TaskProgress
                v-if="analyzeTaskId"
                :task-id="analyzeTaskId"
                title="正在执行 18 项结构化拆标（大模型分段抽取，长文件需要 1–3 分钟）"
                @done="onAnalyzeDone"
                @failed="onAnalyzeFailed"
              />
              <div v-if="analyzeTaskDone || analyzeTaskFailed" class="flex justify-end gap-2 pt-1">
                <el-button @click="resetUpload">重新上传</el-button>
                <el-button v-if="analyzeTaskDone" type="primary" @click="step = 1">查看拆标结果 →</el-button>
              </div>
            </div>
          </section>

          <!-- ============ Step 1：拆标确认 ============ -->
          <section v-else-if="step === 1 && editableAnalysis" class="space-y-5 rise">
            <div v-if="editableAnalysis.extraction_mode === 'rules'" class="note note-warn">
              <el-icon class="text-warn mt-0.5 shrink-0"><WarningFilled /></el-icon>
              <span>{{ editableAnalysis.extraction_note || '当前为正则规则抽取（覆盖度有限），建议在设置中配置大模型后重新拆标' }}</span>
            </div>
            <div v-if="editableAnalysis.source_note" class="note" :class="/图片|扫描/.test(editableAnalysis.source_note) ? 'note-warn' : 'note-info'">
              <el-icon class="mt-0.5 shrink-0"><InfoFilled /></el-icon>
              <span>{{ editableAnalysis.source_note }}</span>
            </div>

            <!-- 基本要素 -->
            <div class="card p-5 sm:p-6">
              <div class="flex items-baseline justify-between gap-2 mb-4">
                <h3 class="text-sm font-semibold text-ink">基本要素</h3>
                <span class="hint">未识别的字段显示为空，可手动补充</span>
              </div>
              <div class="grid sm:grid-cols-2 xl:grid-cols-3 gap-x-4 gap-y-3">
                <div v-for="f in simpleFields" :key="f">
                  <label class="field-label">{{ FIELD_LABELS[f] }}</label>
                  <el-input v-model="editableAnalysis[f]" placeholder="未提及" />
                </div>
              </div>
            </div>

            <!-- 评分细则 -->
            <div class="card overflow-hidden">
              <div class="p-5 sm:p-6 pb-4 flex items-start justify-between gap-3 flex-wrap">
                <div>
                  <h3 class="text-sm font-semibold text-ink flex items-center gap-2">
                    评分细则 <span class="chip chip-accent num">{{ packageItems.length }} 项</span>
                  </h3>
                  <p class="hint mt-1">大纲按评分项对齐，分值决定篇幅。悬停评分项可查看评分标准原文。</p>
                  <p
                    v-if="editableAnalysis.scoring_note"
                    class="text-xs mt-1.5"
                    :class="/不一致|未识别|无法|未抽取/.test(editableAnalysis.scoring_note) ? 'text-warn' : 'text-ink-2'"
                  >{{ editableAnalysis.scoring_note }}</p>
                </div>
                <div v-if="scoringPackages.length > 1" class="flex items-center gap-2 text-xs">
                  <span class="text-ink-3">本次投标分包</span>
                  <div class="seg">
                    <button
                      v-for="pkg in scoringPackages"
                      :key="pkg"
                      class="seg-item"
                      :class="{ 'is-active': editableAnalysis.target_package === pkg }"
                      @click="editableAnalysis.target_package = pkg"
                    >{{ pkg || '默认' }}</button>
                  </div>
                </div>
              </div>
              <div v-if="packageItems.length" class="overflow-x-auto border-t border-line">
                <table class="table-clean min-w-[680px]">
                  <thead>
                    <tr>
                      <th class="!pl-5 sm:!pl-6">评分项</th>
                      <th class="w-24">分值</th>
                      <th class="w-36">大类</th>
                      <th class="w-32">应答方式</th>
                      <th>评分子项</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="it in packageItems" :key="it.id">
                      <td class="!pl-5 sm:!pl-6">
                        <el-tooltip placement="top-start" :show-after="400" :content="it.criteria || '（无评分标准原文）'" popper-class="max-w-md whitespace-pre-wrap">
                          <el-input v-model="it.name" size="small" />
                        </el-tooltip>
                      </td>
                      <td><el-input-number v-model="it.points" size="small" :min="0" :step="0.5" :controls="false" class="!w-16" /></td>
                      <td class="text-ink-2 pt-3">{{ it.category }}<span v-if="it.category_weight != null" class="text-ink-3">（{{ it.category_weight }}%）</span></td>
                      <td>
                        <el-select v-model="it.response_type" size="small">
                          <el-option v-for="(meta, key) in RESPONSE_TYPES" :key="key" :label="meta.label" :value="key" />
                        </el-select>
                      </td>
                      <td class="text-ink-2 pt-3 leading-relaxed">
                        <span v-for="sub in it.sub_items" :key="sub.name" class="inline-block mr-2.5">{{ sub.name }}<span v-if="sub.points != null" class="text-ink-3 num"> {{ sub.points }}分</span></span>
                      </td>
                    </tr>
                  </tbody>
                  <tfoot>
                    <tr>
                      <td class="!pl-5 sm:!pl-6 text-ink-2 font-medium">合计</td>
                      <td class="font-semibold num" :class="Math.abs(packageTotal - 100) > 0.01 ? 'text-warn' : 'text-ok'">{{ packageTotal }}</td>
                      <td colspan="3" class="text-warn">
                        <template v-if="Math.abs(packageTotal - 100) > 0.01">与满分 100 不一致，请对照招标文件核对</template>
                      </td>
                    </tr>
                  </tfoot>
                </table>
              </div>
              <p v-else class="px-6 pb-6 text-xs text-ink-3">未识别到评分细则，大纲将按通用结构规划。</p>
            </div>

            <!-- 红线条款 -->
            <div class="card p-5 sm:p-6">
              <h3 class="text-sm font-semibold text-ink flex items-center gap-2">
                废标红线条款 <span class="chip chip-solid-bad num">{{ editableAnalysis.star_disqualification_items?.length || 0 }} 条</span>
              </h3>
              <p class="hint mt-1 mb-3">不满足即无效投标，请逐条核对。</p>
              <div v-if="Object.keys(editableAnalysis.marker_legend || {}).length" class="rounded-lg bg-raised border border-line px-3 py-2.5 mb-3 text-xs text-ink-2 space-y-1">
                <p class="font-medium text-ink">本文件对条款标记的定义（据此区分红线与扣分项）</p>
                <p v-for="(sentence, mark) in editableAnalysis.marker_legend" :key="mark"><b class="text-bad">{{ mark }}</b> {{ sentence }}</p>
              </div>
              <div class="space-y-2 max-h-[22rem] overflow-auto pr-1">
                <div
                  v-for="(s, i) in editableAnalysis.star_disqualification_items || []"
                  :key="i"
                  class="flex gap-3 items-start"
                >
                  <span class="mt-2 w-5 text-right text-2xs text-bad font-semibold num shrink-0">{{ i + 1 }}</span>
                  <el-input
                    v-model="editableAnalysis.star_disqualification_items[i]"
                    type="textarea"
                    :autosize="{ minRows: 1, maxRows: 4 }"
                    class="redline-input"
                  />
                </div>
              </div>
              <el-button class="mt-3" size="small" text type="primary" @click="editableAnalysis.star_disqualification_items.push('')">
                <el-icon class="mr-1"><Plus /></el-icon>手动补充条款
              </el-button>
            </div>

            <div v-if="editableAnalysis.important_items?.length" class="card p-5 sm:p-6">
              <h3 class="text-sm font-semibold text-ink flex items-center gap-2">
                重要条款 <span class="chip chip-warn num">{{ editableAnalysis.important_items.length }} 条</span>
              </h3>
              <p class="hint mt-1 mb-3">不满足按评分办法扣分，不直接废标。</p>
              <ul class="space-y-1.5 max-h-64 overflow-auto pr-1">
                <li
                  v-for="(s, i) in editableAnalysis.important_items"
                  :key="i"
                  class="text-xs text-ink-2 leading-relaxed border-l-2 border-warn/60 pl-3 py-0.5"
                >{{ s }}</li>
              </ul>
            </div>

            <div class="wizard-footer">
              <el-button @click="step = 0">上一步</el-button>
              <div class="flex gap-2">
                <el-button @click="applyAnalysis">仅保存</el-button>
                <el-button type="primary" @click="applyAnalysis(); step = 2">确认，进入大纲规划 →</el-button>
              </div>
            </div>
          </section>

          <!-- ============ Step 2：大纲规划 ============ -->
          <section v-else-if="step === 2" class="space-y-4 rise">
            <div class="card p-5 sm:p-6 flex items-center justify-between gap-4 flex-wrap">
              <div class="min-w-0">
                <p class="text-sm font-semibold text-ink">一级章节</p>
                <p class="hint mt-1">高分评分项建议独立成章；确认后一键展开完整章节树并按分值分配字数。</p>
                <p v-if="outlineMessage" class="text-xs text-warn mt-1.5">{{ outlineMessage }}</p>
              </div>
              <el-button type="primary" :plain="!!chapters.length" :loading="drafting" @click="draftChapters">
                <el-icon class="mr-1.5"><MagicStick /></el-icon>{{ chapters.length ? '重新 AI 规划' : 'AI 规划一级章节' }}
              </el-button>
            </div>

            <div v-if="chapters.length" class="space-y-2.5">
              <div v-for="(ch, i) in chapters" :key="i" class="card p-3 sm:p-4 flex gap-3 sm:gap-4 group">
                <span class="font-kai text-xl text-ink-3 leading-8 w-7 shrink-0 text-center num">{{ String(i + 1).padStart(2, '0') }}</span>
                <div class="flex-1 min-w-0 space-y-2">
                  <div class="flex gap-2 flex-wrap sm:flex-nowrap">
                    <el-input v-model="ch.title" class="sm:!w-72 shrink-0" placeholder="第X章 …" />
                    <el-input v-model="ch.reqText" class="flex-1 min-w-[12rem]" placeholder="该章须响应的评分点 / 要求（多个用；分隔）" />
                  </div>
                  <el-select
                    v-if="chapterItems.length"
                    v-model="ch.scoring_item_ids"
                    multiple
                    collapse-tags
                    collapse-tags-tooltip
                    :max-collapse-tags="4"
                    class="w-full"
                    placeholder="未关联评分项"
                  >
                    <template #prefix><span class="text-2xs text-ink-3 mr-1">承接</span></template>
                    <el-option v-for="it in chapterItems" :key="it.id" :label="`${it.name}（${it.points ?? '?'}分）`" :value="it.id" />
                  </el-select>
                </div>
                <button class="icon-btn shrink-0 opacity-60 group-hover:opacity-100 hover:!text-bad" title="删除本章" @click="removeChapter(i)">
                  <el-icon><Delete /></el-icon>
                </button>
              </div>
              <button
                class="w-full h-11 rounded-xl border border-dashed border-line-strong text-sm text-ink-3 hover:text-accent-fg hover:border-accent/50 hover:bg-accent-soft/30 transition flex items-center justify-center gap-1.5"
                @click="addChapter"
              >
                <el-icon><Plus /></el-icon>添加章节
              </button>
              <div v-if="uncoveredItems.length" class="note note-warn">
                <el-icon class="text-warn mt-0.5 shrink-0"><WarningFilled /></el-icon>
                <div>
                  <p class="font-medium">{{ uncoveredItems.length }} 个评分项尚无承接章节：{{ uncoveredItems.map((it) => it.name).join('、') }}</p>
                  <p class="text-ink-2 mt-0.5">未承接的评分项在标书中找不到对应内容，评审时可能失分。请将其关联到某个章节或新增章节。</p>
                </div>
              </div>
            </div>
            <EmptyState v-else compact icon="Memo" title="还没有一级章节" description="点击「AI 规划一级章节」按评分细则生成，未配置模型时载入标准结构。" />

            <div class="card p-4 sm:px-6 flex items-center gap-3 flex-wrap text-sm">
              <span class="text-ink-2">全书正文预算</span>
              <el-input-number v-model="totalBudget" :min="5000" :max="200000" :step="5000" />
              <span class="hint">字，按评分比重自动分配到各章节</span>
            </div>

            <div class="wizard-footer">
              <el-button @click="step = 1">上一步</el-button>
              <el-button type="primary" :loading="expanding" @click="expandOutline">展开完整大纲树 →</el-button>
            </div>
          </section>

          <!-- ============ Step 3：全局事实 ============ -->
          <section v-else-if="step === 3" class="space-y-4 rise">
            <div class="note note-info">
              <el-icon class="mt-0.5 text-accent-fg shrink-0"><Lock /></el-icon>
              <span>未填写的字段遵循"不编造"纪律：正文中保持模糊或以【待填写】占位，导出前便于定位补齐。</span>
            </div>
            <div class="card p-5 sm:p-6">
              <div class="grid sm:grid-cols-2 gap-x-4 gap-y-3.5">
                <div v-for="f in FACT_FIELDS" :key="f.k">
                  <label class="field-label">{{ f.l }}</label>
                  <el-input v-model="facts[f.k]" placeholder="留空 = 不编造，【待填写】占位" />
                </div>
                <div>
                  <label class="field-label">未提及事实的处理</label>
                  <el-select v-model="facts.fact_completeness_mode" class="w-full">
                    <el-option label="正文输出【待填写】占位（推荐，导出前易定位）" value="placeholder" />
                    <el-option label="保持模糊表述（如'按合同约定'）" value="omit" />
                  </el-select>
                </div>
              </div>
            </div>

            <div class="wizard-footer">
              <el-button @click="step = 2">上一步</el-button>
              <div class="flex gap-2 flex-wrap justify-end">
                <el-button :loading="busy" @click="skipToWorkspace">暂不填写，先进入编纂台</el-button>
                <el-button type="primary" :loading="busy" @click="saveFactsAndFinish">保存并开始撰写 →</el-button>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  </AppShell>
</template>

<style scoped>
.wizard-footer {
  @apply sticky bottom-0 z-10 flex items-center justify-between gap-3 flex-wrap mt-6 -mx-1 px-1 py-3 border-t border-line;
  background: rgb(var(--c-paper) / 0.88);
  backdrop-filter: blur(8px);
}
.redline-input :deep(.el-textarea__inner) {
  box-shadow: 0 0 0 1px rgb(var(--c-bad) / 0.25) inset;
  background: rgb(var(--c-bad) / 0.035);
}
.redline-input :deep(.el-textarea__inner:focus) {
  box-shadow: 0 0 0 1px rgb(var(--c-bad) / 0.6) inset;
}
</style>
