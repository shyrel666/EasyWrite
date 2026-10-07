<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { useAiStore } from '@/stores/ai'
import AppShell from '@/components/layout/AppShell.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import SettingRow from '@/components/settings/SettingRow.vue'
import ProviderGrid from '@/components/settings/ProviderGrid.vue'
import UsagePanel from '@/components/settings/UsagePanel.vue'
import AppearancePanel from '@/components/settings/AppearancePanel.vue'

const route = useRoute()
const router = useRouter()
const ai = useAiStore()

// ---- 分区：记在地址栏 ?tab=，刷新与分享链接都能回到同一分区 ----
const TABS = [
  {
    key: 'model',
    label: '生成模型',
    icon: 'Cpu',
    description: '拆标、大纲规划、章节撰写、偏离表响应与合规核查共用。保存后立即热重载，无需重启服务。',
  },
  {
    key: 'retrieval',
    label: '语义检索',
    icon: 'Connection',
    description: '知识库的向量召回通道，与生成模型分开配置。不启用时检索退化为关键词召回 + 模型重排，仍可正常使用。',
  },
  {
    key: 'usage',
    label: '调用记录',
    icon: 'Histogram',
    description: '每次真实请求的耗时与用量，含重试与失败；离线演示不计入。用量以服务商返回为准，未返回时不估算。',
  },
  { key: 'appearance', label: '外观', icon: 'Brush', description: '界面主题与显示偏好，只影响本浏览器。' },
]

const tab = computed(() => (TABS.some((t) => t.key === route.query.tab) ? route.query.tab : 'model'))
const tabMeta = computed(() => TABS.find((t) => t.key === tab.value))

function selectTab(key) {
  if (key !== tab.value) router.replace({ query: { ...route.query, tab: key } })
}

// ---- 配置表单 ----
const LLM_FIELDS = ['provider', 'api_key', 'base_url', 'model', 'temperature', 'max_tokens']
const EMB_FIELDS = ['embedding_provider', 'embedding_api_key', 'embedding_base_url', 'embedding_model']
const FIELDS = [...LLM_FIELDS, ...EMB_FIELDS]

const settings = reactive({
  provider: 'deepseek',
  api_key: '',
  api_key_masked: '',
  has_api_key: false,
  base_url: '',
  model: '',
  temperature: 0.3,
  max_tokens: 4096,
  embedding_provider: 'none',
  embedding_api_key: '',
  embedding_base_url: '',
  embedding_model: '',
  presets: {},
  retired_models: {},
  presets_reviewed: '',
})

const loaded = ref(false)
const loadError = ref('')
const saved = ref({}) // 上次保存（或载入）时的字段值，用于判断未保存修改与撤销
const saving = ref(false)
const embeddingDesc = ref(null)

function pick(fields) {
  return Object.fromEntries(fields.map((k) => [k, settings[k]]))
}
function changed(fields) {
  return loaded.value && fields.some((k) => settings[k] !== saved.value[k])
}
const dirtyModel = computed(() => changed(LLM_FIELDS))
const dirtyRetrieval = computed(() => changed(EMB_FIELDS))
const dirty = computed(() => dirtyModel.value || dirtyRetrieval.value)
const TAB_DIRTY = { model: dirtyModel, retrieval: dirtyRetrieval }

async function load() {
  loadError.value = ''
  try {
    const [cfg, emb] = await Promise.all([api.getAiSettings(), api.embeddingDescribe().catch(() => null)])
    Object.assign(settings, cfg)
    embeddingDesc.value = emb
    saved.value = pick(FIELDS)
    loaded.value = true
  } catch (e) {
    loadError.value = e.message
  }
}

async function save({ quiet = false } = {}) {
  saving.value = true
  try {
    const res = await api.updateAiSettings(pick(FIELDS))
    // 回写服务端结果：密钥框清空、"已保存"掩码更新
    Object.assign(settings, res.settings || {})
    saved.value = pick(FIELDS)
    await ai.refresh()
    try {
      embeddingDesc.value = await api.embeddingDescribe()
    } catch { /* 只用于展示 */ }
    if (!quiet) ElMessage.success('已保存，配置即时生效')
    return true
  } catch (e) {
    ElMessage.error('保存失败：' + e.message)
    return false
  } finally {
    saving.value = false
  }
}

function discard() {
  Object.assign(settings, saved.value)
  testResult.value = null
}

onBeforeRouteLeave(async () => {
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm('当前修改尚未保存，离开后将丢失。', '放弃未保存的修改？', {
      confirmButtonText: '放弃并离开',
      cancelButtonText: '留在此页',
      type: 'warning',
    })
    return true
  } catch {
    return false
  }
})

function hostOf(url) {
  if (!url) return ''
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

// ---- 生成模型 ----
const currentPreset = computed(() => settings.presets?.[settings.provider] || {})
const fetched = ref({ provider: '', models: [], source: '' })
const fetchingModels = ref(false)
const testing = ref(false)
const testResult = ref(null)

// 已停用型号：后端按各家停用公告维护，已保存的配置用到时提示一键替换
const retiredInfo = computed(() => settings.retired_models?.[settings.model] || null)
const activeRetired = computed(() => (ai.llmConfigured ? settings.retired_models?.[ai.llmModel] || null : null))

const modelOptions = computed(() =>
  fetched.value.provider === settings.provider && fetched.value.models.length
    ? fetched.value.models
    : currentPreset.value.available_models || []
)

const TOKEN_STEPS = [
  { value: 2048, label: '2K', text: '简短章节' },
  { value: 4096, label: '4K', text: '常规章节，适合大多数情况' },
  { value: 8192, label: '8K', text: '长章节' },
  { value: 16384, label: '16K', text: '超长综合大章，单次耗时更久' },
]
const tokenSteps = computed(() => {
  if (TOKEN_STEPS.some((s) => s.value === settings.max_tokens)) return TOKEN_STEPS
  const n = settings.max_tokens
  return [...TOKEN_STEPS, { value: n, label: `${Math.round(n / 1024)}K`, text: '自定义上限' }].sort((a, b) => a.value - b.value)
})
const tokenHint = computed(() => tokenSteps.value.find((s) => s.value === settings.max_tokens)?.text || '')

function applyPreset(key) {
  settings.provider = key
  const preset = settings.presets[key]
  if (preset) {
    if (preset.base_url) settings.base_url = preset.base_url
    if (preset.default_model) settings.model = preset.default_model
  }
  testResult.value = null
  if (settings.embedding_provider === 'none') {
    // 首次切换到支持嵌入的供应商时，顺带给嵌入配置一个默认建议
    const embPreset = embeddingDesc.value?.presets?.[key]
    if (embPreset?.models?.length) {
      settings.embedding_provider = key
      settings.embedding_base_url = embPreset.base_url
      settings.embedding_model = embPreset.models[0]
    }
  }
}

async function fetchModels() {
  fetchingModels.value = true
  try {
    const res = await api.fetchModels({ provider: settings.provider, api_key: settings.api_key, base_url: settings.base_url })
    fetched.value = { provider: settings.provider, models: res.models || [], source: res.source }
    ElMessage.success(res.message)
  } catch (e) {
    ElMessage.error('获取失败：' + e.message)
  } finally {
    fetchingModels.value = false
  }
}

async function testConnection() {
  testing.value = true
  testResult.value = null
  try {
    // 密钥框留空 = 用已保存的密钥测试（undefined 不会被序列化，后端回退到已保存配置）
    testResult.value = await api.testAi({ api_key: settings.api_key || undefined, base_url: settings.base_url, model: settings.model })
  } catch (e) {
    testResult.value = { ok: false, error: '测试失败：' + e.message }
  } finally {
    testing.value = false
  }
}

// ---- 语义检索 ----
const embeddingOn = computed(() => settings.embedding_provider && settings.embedding_provider !== 'none')
const embeddingModels = computed(() => embeddingDesc.value?.presets?.[settings.embedding_provider]?.models || [])
const testingEmbedding = ref(false)
const embeddingTestResult = ref(null)

const PIPELINE = [
  { label: '多路查询', text: '扩写成多条查询' },
  { lane: true },
  { label: 'RRF 融合', text: '按名次合并' },
  { label: '模型重排', text: '逐条 0–10 打分' },
  { label: '阈值过滤', text: '低分宁缺毋滥' },
  { label: '上下文扩展', text: '补全所属小节' },
]

function applyEmbeddingPreset(key) {
  settings.embedding_provider = key
  const preset = embeddingDesc.value?.presets?.[key]
  if (preset) {
    if (preset.base_url) settings.embedding_base_url = preset.base_url
    if (preset.models?.length) settings.embedding_model = preset.models[0]
  }
  embeddingTestResult.value = null
}

async function testEmbedding() {
  testingEmbedding.value = true
  embeddingTestResult.value = null
  try {
    // 嵌入探针读取的是已保存的配置，所以先保存
    if (!(await save({ quiet: true }))) return
    embeddingTestResult.value = await api.testEmbedding()
  } catch (e) {
    embeddingTestResult.value = { ok: false, message: '测试失败：' + e.message }
  } finally {
    testingEmbedding.value = false
  }
}

onMounted(load)
</script>

<template>
  <AppShell active="settings">
    <div class="max-w-4xl mx-auto px-4 sm:px-8 py-6 sm:py-10">
      <PageHeader title="系统设置" description="模型服务、知识检索与界面偏好。修改后即时热重载，无需重启服务。">
        <template #actions>
          <button class="inline-flex items-center gap-1.5 text-xs text-ink-2 hover:text-accent-fg transition-colors" @click="router.push('/about')">
            <el-icon><InfoFilled /></el-icon>关于 EasyWrite
          </button>
        </template>
      </PageHeader>

      <!-- 分区标签：滚动时吸顶 -->
      <nav class="settings-tabs" role="tablist" aria-label="设置分区">
        <button
          v-for="t in TABS"
          :key="t.key"
          role="tab"
          :aria-selected="tab === t.key"
          class="settings-tab"
          :class="{ 'is-active': tab === t.key }"
          @click="selectTab(t.key)"
        >
          <el-icon :size="15"><component :is="t.icon" /></el-icon>
          <span>{{ t.label }}</span>
          <span v-if="TAB_DIRTY[t.key]?.value" class="text-2xs font-normal text-warn">未保存</span>
          <span v-else-if="t.key === 'model'" class="dot" :class="ai.llmConfigured ? 'bg-ok' : 'bg-warn'" />
          <span v-else-if="t.key === 'retrieval'" class="dot" :class="ai.embeddingAvailable ? 'bg-ok' : 'bg-line-strong'" />
        </button>
      </nav>
      <p :key="tab" class="mt-4 mb-5 text-xs text-ink-2 leading-relaxed rise">{{ tabMeta.description }}</p>

      <div v-if="loadError && ['model', 'retrieval'].includes(tab)" class="note note-bad">
        <el-icon class="mt-0.5"><CircleCloseFilled /></el-icon>
        <span class="flex-1">配置加载失败：{{ loadError }}</span>
        <button class="font-medium text-accent-fg hover:underline" @click="load">重试</button>
      </div>

      <!-- ======= 生成模型 ======= -->
      <div v-else-if="tab === 'model'" class="space-y-5">
        <!-- 当前状态 -->
        <section class="card overflow-hidden rise">
          <div class="p-5 sm:p-6 flex items-center gap-4 flex-wrap">
            <span class="status-orb" :class="ai.llmConfigured ? 'is-ok' : 'is-warn'">
              <el-icon :size="20"><Cpu /></el-icon>
            </span>
            <div class="flex-1 min-w-0">
              <p class="text-2xs text-ink-3">当前生效</p>
              <p class="mt-0.5 flex items-center gap-2 min-w-0">
                <span class="text-base font-semibold truncate" :class="ai.llmConfigured ? 'text-ink' : 'text-warn'">
                  {{ ai.llmConfigured ? ai.llmModel : '未配置 · 离线演示模式' }}
                </span>
                <span v-if="activeRetired" class="chip chip-bad shrink-0">已停用</span>
              </p>
              <p class="mt-0.5 text-xs text-ink-3 truncate" :class="{ 'font-mono': ai.llmConfigured }">
                {{ ai.llmConfigured ? hostOf(saved.base_url) : '生成内容为内置演示样例，不代表真实 AI 输出' }}
              </p>
            </div>
            <el-tooltip content="用下方当前填写的配置测试（含未保存的修改）" placement="top">
              <el-button :loading="testing" :disabled="!loaded" @click="testConnection">
                <el-icon class="mr-1.5"><Promotion /></el-icon>测试连通性
              </el-button>
            </el-tooltip>
          </div>
          <div
            v-if="testResult"
            class="px-5 sm:px-6 py-2.5 border-t text-xs flex items-start gap-2"
            :class="testResult.ok ? 'border-ok/20 bg-ok/[0.06] text-ok' : 'border-bad/20 bg-bad/[0.06] text-bad'"
          >
            <el-icon class="mt-0.5 shrink-0"><component :is="testResult.ok ? 'CircleCheckFilled' : 'CircleCloseFilled'" /></el-icon>
            <span class="break-all">{{ testResult.ok ? testResult.message : testResult.error }}</span>
          </div>
        </section>

        <template v-if="loaded">
          <section class="card rise" style="animation-delay: 40ms">
            <div class="card-head">
              <h3>服务商</h3>
              <p class="hint">选择预设会填入接口地址与默认模型；其他 OpenAI 兼容接口选「自定义」。</p>
            </div>
            <div class="p-4 sm:p-5">
              <ProviderGrid :presets="settings.presets" :model-value="settings.provider" @select="applyPreset" />
            </div>
          </section>

          <section class="card divide-y divide-line rise" style="animation-delay: 80ms">
            <div class="card-head !border-b-0">
              <h3>连接</h3>
            </div>
            <SettingRow label="API Key" hint="只保存在本机配置文件，不会回传到页面。">
              <template #badge>
                <span v-if="settings.has_api_key" class="chip chip-ok font-mono">已保存 {{ settings.api_key_masked }}</span>
                <span v-else class="chip chip-warn">未填写</span>
              </template>
              <el-input
                v-model="settings.api_key"
                type="password"
                show-password
                autocomplete="off"
                :placeholder="settings.has_api_key ? '留空则沿用已保存的密钥' : 'sk-…'"
              />
            </SettingRow>
            <SettingRow label="接口地址" hint="OpenAI 兼容的 Base URL，选择服务商预设会自动填写。">
              <el-input v-model="settings.base_url" placeholder="https://api.example.com/v1" />
            </SettingRow>
            <SettingRow label="模型">
              <template #hint>
                可直接输入模型名；刷新按钮联网拉取服务商的实时列表，已停用的型号会被隐藏。
                <template v-if="fetched.provider === settings.provider && fetched.source === 'online_api'">
                  当前列表来自服务商接口（{{ fetched.models.length }} 个）。
                </template>
                <template v-else-if="settings.presets_reviewed">推荐清单核对于 {{ settings.presets_reviewed }}。</template>
              </template>
              <div class="flex gap-2">
                <el-select v-model="settings.model" filterable allow-create default-first-option class="flex-1 min-w-0" placeholder="选择或输入模型名">
                  <el-option v-for="m in modelOptions" :key="m" :label="m" :value="m" />
                </el-select>
                <el-tooltip content="联网拉取服务商的可用模型" placement="top">
                  <el-button :loading="fetchingModels" @click="fetchModels"><el-icon><Refresh /></el-icon></el-button>
                </el-tooltip>
              </div>
              <div v-if="retiredInfo" class="note note-warn mt-2.5">
                <el-icon class="mt-0.5 text-warn"><WarningFilled /></el-icon>
                <span class="flex-1 min-w-0">
                  <span class="font-mono">{{ settings.model }}</span> {{ retiredInfo.note }}，建议改用
                  <span class="font-mono">{{ retiredInfo.replacement }}</span>。
                </span>
                <button class="font-medium text-accent-fg hover:underline whitespace-nowrap" @click="settings.model = retiredInfo.replacement">
                  一键替换
                </button>
              </div>
              <p v-if="currentPreset.note" class="hint mt-2">{{ currentPreset.note }}</p>
            </SettingRow>
          </section>

          <section class="card divide-y divide-line rise" style="animation-delay: 120ms">
            <div class="card-head !border-b-0">
              <h3>生成参数</h3>
            </div>
            <SettingRow label="温度" hint="越低措辞越稳定。标书写作建议 0.2–0.4。">
              <div class="flex items-center gap-4">
                <span class="text-2xs text-ink-3 shrink-0">严谨</span>
                <el-slider v-model="settings.temperature" :min="0" :max="1" :step="0.05" :show-tooltip="false" class="flex-1" />
                <span class="text-2xs text-ink-3 shrink-0">发散</span>
                <span class="num w-10 text-right text-sm font-semibold text-ink">{{ Number(settings.temperature).toFixed(2) }}</span>
              </div>
            </SettingRow>
            <SettingRow label="单次最大输出" hint="推理模型的思考过程也计入；输出被截断时会自动放大额度重试。">
              <div class="flex items-center gap-3 flex-wrap">
                <div class="seg">
                  <button
                    v-for="s in tokenSteps"
                    :key="s.value"
                    class="seg-item num min-w-[3rem]"
                    :class="{ 'is-active': settings.max_tokens === s.value }"
                    @click="settings.max_tokens = s.value"
                  >
                    {{ s.label }}
                  </button>
                </div>
                <span class="text-xs text-ink-2">{{ tokenHint }}</span>
              </div>
            </SettingRow>
          </section>
        </template>
        <div v-else class="h-72 rounded-xl skeleton" />
      </div>

      <!-- ======= 语义检索 ======= -->
      <div v-else-if="tab === 'retrieval'" class="space-y-5">
        <section class="card rise">
          <div class="px-5 sm:px-6 pt-4 pb-3.5 border-b border-line flex items-start justify-between gap-3 flex-wrap">
            <div>
              <h3 class="text-[13px] font-semibold text-ink">检索链路</h3>
              <p class="hint mt-1">每次撰写章节时，从知识库找参考资料的过程。</p>
            </div>
            <span class="chip" :class="ai.embeddingAvailable ? 'chip-ok' : 'chip-mute'">
              <span class="dot" :class="ai.embeddingAvailable ? 'bg-ok' : 'bg-ink-3'" />
              {{ ai.embeddingAvailable ? `向量召回已启用 · ${ai.embeddingModel}` : '向量召回未启用' }}
            </span>
          </div>
          <div class="p-5 sm:p-6 overflow-x-auto">
            <ol class="pipeline">
              <template v-for="(step, i) in PIPELINE" :key="i">
                <li v-if="step.lane" class="flex flex-col gap-1.5">
                  <div class="pipe-node">
                    <p class="pipe-title">关键词召回</p>
                    <p class="pipe-text">BM25 · jieba 分词</p>
                  </div>
                  <div class="pipe-node" :class="ai.embeddingAvailable ? 'is-on' : 'is-off'">
                    <p class="pipe-title">向量召回</p>
                    <p class="pipe-text">{{ ai.embeddingAvailable ? '语义相近即可命中' : '未启用，跳过' }}</p>
                  </div>
                </li>
                <li v-else class="pipe-node justify-center">
                  <p class="pipe-title">{{ step.label }}</p>
                  <p class="pipe-text">{{ step.text }}</p>
                </li>
                <li v-if="i < PIPELINE.length - 1" class="flex items-center text-ink-3" aria-hidden="true">
                  <el-icon :size="12"><ArrowRight /></el-icon>
                </li>
              </template>
            </ol>
          </div>
        </section>

        <template v-if="loaded">
          <section class="card rise" style="animation-delay: 40ms">
            <div class="card-head">
              <h3>嵌入服务</h3>
              <p class="hint">推荐硅基流动的 BAAI/bge-m3（注册送额度），内网环境可用本地 Ollama。</p>
            </div>
            <div class="p-4 sm:p-5">
              <ProviderGrid
                :presets="embeddingDesc?.presets || {}"
                :model-value="settings.embedding_provider"
                none-label="仅关键词召回"
                @select="applyEmbeddingPreset"
              />
            </div>
          </section>

          <section v-if="embeddingOn" class="card divide-y divide-line rise" style="animation-delay: 80ms">
            <div class="card-head !border-b-0">
              <h3>连接</h3>
            </div>
            <SettingRow label="API Key" hint="本地 Ollama 可留空。">
              <el-input v-model="settings.embedding_api_key" type="password" show-password autocomplete="off" placeholder="sk-…" />
            </SettingRow>
            <SettingRow label="接口地址">
              <el-input v-model="settings.embedding_base_url" placeholder="https://api.siliconflow.cn/v1" />
            </SettingRow>
            <SettingRow label="嵌入模型" hint="更换模型后，已入库文档需在「知识库」里重建向量。">
              <el-select v-model="settings.embedding_model" filterable allow-create default-first-option class="w-full" placeholder="选择或输入模型名">
                <el-option v-for="m in embeddingModels" :key="m" :label="m" :value="m" />
              </el-select>
            </SettingRow>
            <div class="px-5 sm:px-6 py-3.5 bg-raised rounded-b-xl flex items-center gap-3 flex-wrap">
              <el-button :loading="testingEmbedding" @click="testEmbedding">
                <el-icon class="mr-1.5"><Promotion /></el-icon>保存并测试
              </el-button>
              <span
                v-if="embeddingTestResult"
                class="text-xs flex items-center gap-1.5"
                :class="embeddingTestResult.ok ? 'text-ok' : 'text-warn'"
              >
                <el-icon><component :is="embeddingTestResult.ok ? 'CircleCheckFilled' : 'WarningFilled'" /></el-icon>
                {{ embeddingTestResult.message }}
              </span>
              <span v-else class="hint">嵌入探针读取已保存的配置，所以会先保存。</span>
            </div>
          </section>
          <div v-else class="note note-info rise" style="animation-delay: 80ms">
            <el-icon class="mt-0.5 text-accent-fg"><InfoFilled /></el-icon>
            <span>不启用时只走关键词召回，再由生成模型重排与把关。措辞与知识库差别较大时可能漏召回，启用向量召回可以弥补。</span>
          </div>
        </template>
        <div v-else class="h-56 rounded-xl skeleton" />
      </div>

      <!-- ======= 调用记录 ======= -->
      <UsagePanel v-else-if="tab === 'usage'" />

      <!-- ======= 外观 ======= -->
      <AppearancePanel v-else-if="tab === 'appearance'" class="rise" />

      <!-- 未保存修改提示条 -->
      <transition
        enter-active-class="transition duration-200 ease-out"
        enter-from-class="opacity-0 translate-y-3"
        leave-active-class="transition duration-150 ease-in"
        leave-to-class="opacity-0 translate-y-3"
      >
        <div v-if="dirty && ['model', 'retrieval'].includes(tab)" class="sticky bottom-4 sm:bottom-6 z-20 mt-6">
          <div class="savebar">
            <span class="dot bg-warn" />
            <span class="flex-1 min-w-0 text-[13px] text-ink truncate">
              有未保存的修改<span class="hidden sm:inline text-ink-3">
                · {{ [dirtyModel && '生成模型', dirtyRetrieval && '语义检索'].filter(Boolean).join('、') }}</span>
            </span>
            <el-button text :disabled="saving" @click="discard">撤销</el-button>
            <el-button type="primary" :loading="saving" @click="save()">保存</el-button>
          </div>
        </div>
      </transition>
    </div>
  </AppShell>
</template>

<style scoped>
.settings-tabs {
  @apply sticky top-0 z-20 -mx-4 px-4 sm:mx-0 sm:px-0 flex gap-1 overflow-x-auto border-b border-line bg-paper;
  scrollbar-width: none;
}
.settings-tabs::-webkit-scrollbar {
  display: none;
}
.settings-tab {
  @apply relative inline-flex items-center gap-1.5 h-11 px-3 text-[13px] text-ink-2 whitespace-nowrap transition-colors;
}
.settings-tab:hover {
  @apply text-ink;
}
.settings-tab.is-active {
  @apply text-ink font-medium;
}
.settings-tab.is-active::after {
  content: '';
  @apply absolute left-2 right-2 -bottom-px h-[2px] rounded-full bg-accent;
}

.card-head {
  @apply flex flex-col gap-1 px-5 sm:px-6 pt-4 pb-3.5 border-b border-line;
}
.card-head h3 {
  @apply text-[13px] font-semibold text-ink;
}

.status-orb {
  @apply relative w-11 h-11 shrink-0 rounded-xl flex items-center justify-center;
}
.status-orb.is-ok {
  @apply bg-ok/10 text-ok;
}
.status-orb.is-warn {
  @apply bg-warn/10 text-warn;
}
.status-orb::after {
  content: '';
  @apply absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full ring-2 ring-surface;
}
.status-orb.is-ok::after {
  @apply bg-ok;
}
.status-orb.is-warn::after {
  @apply bg-warn;
}

.pipeline {
  @apply grid gap-1.5 min-w-[600px];
  grid-template-columns: 1fr auto 1.2fr auto 1fr auto 1fr auto 1fr auto 1fr;
}
.pipe-node {
  @apply flex flex-col rounded-lg border border-line bg-raised px-2.5 py-2;
}
.pipe-node.is-on {
  @apply border-ok/30 bg-ok/[0.06];
}
.pipe-node.is-off {
  @apply border-dashed bg-transparent opacity-70;
}
.pipe-title {
  @apply text-xs font-medium text-ink whitespace-nowrap;
}
.pipe-text {
  @apply text-2xs text-ink-3 mt-0.5;
}

.savebar {
  @apply mx-auto max-w-xl flex items-center gap-3 rounded-xl border border-line bg-surface pl-4 pr-2 py-2 shadow-float;
}
</style>
