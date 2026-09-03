<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useProjectStore } from '@/stores/project'
import { useAiStore } from '@/stores/ai'
import AppShell from '@/components/layout/AppShell.vue'
import TaskProgress from '@/components/common/TaskProgress.vue'

const route = useRoute()
const router = useRouter()
const store = useProjectStore()
const ai = useAiStore()

const projectId = route.params.id
const step = ref(0)
const busy = ref(false)

// ---------- Step 0: 上传招标文件 ----------
const fileInput = ref(null)
const uploading = ref(false)
const analyzeTaskId = ref('')
const analyzeTaskDone = ref(false)
const pasteMode = ref(false)
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

async function handleFileUpload(event) {
  const file = event.target.files[0]
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.docx')) {
    ElMessage.warning('仅支持 .docx 格式招标文件')
    return
  }
  uploading.value = true
  analyzeTaskDone.value = false
  try {
    const res = await api.analyzeTender(file)
    analyzeTaskId.value = res.task_id
  } catch (e) {
    ElMessage.error('上传失败：' + e.message)
    uploading.value = false
  }
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
  if (task.status !== 'completed' || !task.result) return
  analyzeTaskDone.value = true
  analysis.value = task.result
  editableAnalysis.value = { ...task.result }
  if (task.result.extraction_mode === 'rules') {
    ElMessage.warning('当前为正则规则抽取（覆盖度有限）。配置大模型后可获得完整 18 项拆解。')
  }
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
  chapters.value.push({ title: '', requirements: [], reqText: '' })
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
    }))
  }
  // 初始步骤：有正文进入编纂；有大纲进入大纲步；否则从头开始
  if (p.outline?.length) step.value = 3
  else if (p.tender_analysis) step.value = 1
})
</script>

<template>
  <AppShell :project-id="projectId" :project-name="store.project?.name" active="">
    <div class="max-w-4xl mx-auto px-4 py-6 pb-16 md:pb-6">
      <!-- 步骤条 -->
      <el-steps :active="step" align-center class="mb-8" finish-status="success">
        <el-step title="上传招标文件" description="18项拆标" />
        <el-step title="确认拆标结果" description="★红线条款" />
        <el-step title="规划大纲" description="评分项对齐" />
        <el-step title="全局事实" description="硬约束设定" />
      </el-steps>

      <!-- ============ Step 0 ============ -->
      <section v-if="step === 0" class="bg-white rounded-xl border border-slate-200 p-6">
        <h2 class="font-bold text-slate-800 mb-1">上传招标文件</h2>
        <p class="text-sm text-slate-500 mb-5">
          上传 .docx 格式招标文件，系统将自动执行 18 项核心要素结构化拆解（★号条款、评分办法、资质门槛等）。
        </p>

        <div v-if="!analyzeTaskId && !pasteMode" class="text-center py-10">
          <div
            class="border-2 border-dashed border-slate-300 rounded-xl py-12 px-6 hover:border-blue-400 hover:bg-blue-50/40 transition cursor-pointer"
            @click="fileInput.click()"
          >
            <el-icon :size="40" class="text-slate-300"><UploadFilled /></el-icon>
            <p class="mt-3 text-sm font-medium text-slate-600">点击选择 .docx 招标文件</p>
            <p class="mt-1 text-xs text-slate-400">也可在下方粘贴招标文件正文</p>
          </div>
          <input ref="fileInput" type="file" accept=".docx" class="hidden" @change="handleFileUpload" />
          <button class="mt-4 text-xs text-blue-600 underline" @click="pasteMode = true">
            没有文件？粘贴正文文本 →
          </button>
        </div>

        <div v-else-if="pasteMode && !analyzeTaskId" class="space-y-3">
          <el-input
            v-model="pasteText"
            type="textarea"
            :rows="12"
            placeholder="粘贴招标文件关键章节正文（第一章投标须知、评分办法、技术要求部分越完整效果越好）"
          />
          <div class="flex gap-2 justify-end">
            <el-button @click="pasteMode = false">返回上传</el-button>
            <el-button type="primary" :loading="uploading" @click="runPasteAnalysis">执行18项拆标</el-button>
          </div>
        </div>

        <div v-else class="space-y-3">
          <TaskProgress
            v-if="analyzeTaskId"
            :task-id="analyzeTaskId"
            title="正在执行 18 项结构化拆标（大模型分段抽取，长文件需要 1-3 分钟）"
            @done="onAnalyzeDone"
            @failed="(t) => { uploading = false; ElMessage.error('拆标失败：' + t.error) }"
          />
          <div v-if="analyzeTaskDone" class="flex justify-end">
            <el-button type="primary" @click="step = 1">查看拆标结果 →</el-button>
          </div>
        </div>
      </section>

      <!-- ============ Step 1: 拆标确认 ============ -->
      <section v-else-if="step === 1" class="bg-white rounded-xl border border-slate-200 p-6">
        <div class="flex items-center justify-between mb-4 flex-wrap gap-2">
          <h2 class="font-bold text-slate-800">确认 18 项拆标结果</h2>
          <div class="flex gap-2">
            <el-button size="small" @click="step = 0">重新上传</el-button>
            <el-button size="small" @click="applyAnalysis">保存并应用</el-button>
          </div>
        </div>

        <el-alert
          v-if="editableAnalysis?.extraction_mode === 'rules'"
          type="warning"
          :closable="false"
          class="mb-4"
          :title="editableAnalysis?.extraction_note || '当前为正则规则抽取（覆盖度有限），建议在设置中配置大模型后重新拆标'"
        />

        <!-- 简单字段 -->
        <div class="grid sm:grid-cols-2 gap-3 mb-5">
          <div v-for="f in simpleFields" :key="f" class="text-sm">
            <span class="text-slate-500 block text-xs mb-0.5">{{ FIELD_LABELS[f] }}</span>
            <el-input v-model="editableAnalysis[f]" size="small" placeholder="未提及" />
          </div>
        </div>

        <!-- ★条款 -->
        <h3 class="text-sm font-bold text-slate-800 mb-2 flex items-center gap-2">
          <el-tag type="danger" size="small" effect="dark">★号不可偏离条款 {{ editableAnalysis?.star_disqualification_items?.length || 0 }} 条</el-tag>
          <span class="text-xs text-slate-400 font-normal">逐条核对，漏一条即废标风险</span>
        </h3>
        <div class="space-y-2 max-h-72 overflow-auto pr-1">
          <div
            v-for="(s, i) in editableAnalysis?.star_disqualification_items || []"
            :key="i"
            class="flex gap-2 items-start text-sm border-l-4 border-red-400 bg-red-50 rounded p-2"
          >
            <el-input v-model="editableAnalysis.star_disqualification_items[i]" size="small" type="textarea" :autosize="{ minRows: 1, maxRows: 3 }" />
          </div>
          <el-button size="small" text type="primary" @click="editableAnalysis.star_disqualification_items.push('')">+ 手动补充条款</el-button>
        </div>

        <div class="flex justify-between mt-6">
          <el-button @click="step = 0">上一步</el-button>
          <el-button type="primary" @click="applyAnalysis(); step = 2">确认，进入大纲规划 →</el-button>
        </div>
      </section>

      <!-- ============ Step 2: 大纲规划 ============ -->
      <section v-else-if="step === 2" class="bg-white rounded-xl border border-slate-200 p-6">
        <h2 class="font-bold text-slate-800 mb-1">技术标大纲规划（评分项对齐）</h2>
        <p class="text-sm text-slate-500 mb-4">
          先确认一级章节结构（评分高分的评分项建议独立成章），再一键展开完整章节树并自动分配字数预算。
        </p>

        <el-button type="primary" plain :loading="drafting" class="mb-4" @click="draftChapters">
          <el-icon class="mr-1"><MagicStick /></el-icon>{{ chapters.length ? '重新 AI 规划一级章节' : 'AI 规划一级章节' }}
        </el-button>
        <p v-if="outlineMessage" class="text-xs text-amber-600 mb-3">{{ outlineMessage }}</p>

        <div v-if="chapters.length" class="space-y-2 mb-5">
          <div v-for="(ch, i) in chapters" :key="i" class="flex gap-2 items-start">
            <el-input v-model="ch.title" size="small" class="!w-60 sm:!w-72 shrink-0" placeholder="第X章 …" />
            <el-input
              v-model="ch.reqText"
              size="small"
              class="flex-1"
              placeholder="该章须响应的评分点/要求（多个用；分隔）"
            />
            <el-button size="small" text type="danger" @click="removeChapter(i)"><el-icon><Delete /></el-icon></el-button>
          </div>
          <el-button size="small" text type="primary" @click="addChapter">+ 添加章节</el-button>
        </div>

        <div class="flex items-center gap-4 flex-wrap">
          <div class="flex items-center gap-2 text-sm">
            <span class="text-slate-600">全书正文预算：</span>
            <el-input-number v-model="totalBudget" :min="5000" :max="200000" :step="5000" size="small" />
            <span class="text-xs text-slate-400">字（按评分比重自动分配）</span>
          </div>
        </div>

        <div class="flex justify-between mt-6">
          <el-button @click="step = 1">上一步</el-button>
          <el-button type="primary" :loading="expanding" @click="expandOutline">展开完整大纲树 →</el-button>
        </div>
      </section>

      <!-- ============ Step 3: 全局事实 ============ -->
      <section v-else class="bg-white rounded-xl border border-slate-200 p-6">
        <h2 class="font-bold text-slate-800 mb-1">全局事实硬约束</h2>
        <p class="text-sm text-slate-500 mb-5">
          这些事实将以只读硬约束贯穿全书撰写，杜绝前后矛盾（如公司名、产品名、数据库选型前后不一致）。
          未填写的字段遵循"不编造"纪律：正文中保持模糊或以【待填写】占位。
        </p>

        <div class="grid sm:grid-cols-2 gap-3">
          <div v-for="f in [
            { k: 'company_name', l: '投标企业法定全称' },
            { k: 'credit_code', l: '统一社会信用代码' },
            { k: 'legal_rep', l: '法定代表人' },
            { k: 'registered_capital', l: '注册资本' },
            { k: 'core_product_name', l: '核心产品/平台名称' },
            { k: 'architecture_stack', l: '统一技术架构路线' },
            { k: 'database_selection', l: '数据库底座选型' },
            { k: 'sla_commitment', l: 'SLA 售后承诺' },
            { k: 'delivery_guarantee', l: '工期交付承诺' },
          ]" :key="f.k" class="text-sm">
            <span class="text-slate-500 block text-xs mb-0.5">{{ f.l }}</span>
            <el-input v-model="facts[f.k]" size="small" placeholder="留空 = 不编造，【待填写】占位" />
          </div>
          <div class="text-sm">
            <span class="text-slate-500 block text-xs mb-0.5">未提及事实的完备纪律</span>
            <el-select v-model="facts.fact_completeness_mode" size="small" class="w-full">
              <el-option label="正文输出【待填写】占位（推荐，导出前易定位）" value="placeholder" />
              <el-option label="保持模糊表述（如'按合同约定'）" value="omit" />
            </el-select>
          </div>
        </div>

        <div class="flex justify-between mt-6">
          <el-button @click="step = 2">上一步</el-button>
          <div class="flex gap-2">
            <el-button :loading="busy" @click="skipToWorkspace">暂不填写，先进入编纂台</el-button>
            <el-button type="primary" :loading="busy" @click="saveFactsAndFinish">保存并开始撰写 →</el-button>
          </div>
        </div>
      </section>
    </div>
  </AppShell>
</template>
