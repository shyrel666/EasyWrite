<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAiStore } from '@/stores/ai'
import AppShell from '@/components/layout/AppShell.vue'

const ai = useAiStore()

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
})

const testing = ref(false)
const testResult = ref(null)
const fetchingModels = ref(false)
const modelsSource = ref('')
const saving = ref(false)
const embeddingDesc = ref(null)
const testingEmbedding = ref(false)
const embeddingTestResult = ref(null)

const currentPreset = computed(() => settings.presets?.[settings.provider] || {})

async function load() {
  Object.assign(settings, await api.getAiSettings())
  try {
    embeddingDesc.value = await api.embeddingDescribe()
  } catch { /* ignore */ }
}

function applyPreset(presetKey) {
  settings.provider = presetKey
  const preset = settings.presets[presetKey]
  if (preset) {
    if (preset.base_url) settings.base_url = preset.base_url
    if (preset.default_model) settings.model = preset.default_model
  }
  if (settings.embedding_provider === 'none') {
    // 首次切换到支持嵌入的供应商时给嵌入配置一个默认建议
    const embPreset = embeddingDesc.value?.presets?.[presetKey]
    if (embPreset?.models?.length) {
      settings.embedding_provider = presetKey
      settings.embedding_base_url = embPreset.base_url
      settings.embedding_model = embPreset.models[0]
    }
  }
}

async function save() {
  saving.value = true
  try {
    const payload = { ...settings }
    delete payload.presets
    await api.updateAiSettings(payload)
    await ai.refresh()
    ElMessage.success('配置已保存并热重载')
  } catch (e) {
    ElMessage.error('保存失败：' + e.message)
  } finally {
    saving.value = false
  }
}

async function testConnection() {
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await api.testAi({ api_key: settings.api_key, base_url: settings.base_url, model: settings.model })
    if (testResult.value.ok) ElMessage.success(testResult.value.message)
    else ElMessage.error(testResult.value.error)
  } catch (e) {
    ElMessage.error('测试失败：' + e.message)
  } finally {
    testing.value = false
  }
}

async function fetchModels() {
  fetchingModels.value = true
  try {
    const res = await api.fetchModels({ provider: settings.provider, api_key: settings.api_key, base_url: settings.base_url })
    modelsSource.value = res.source
    ElMessage.success(res.message)
  } catch (e) {
    ElMessage.error('获取失败：' + e.message)
  } finally {
    fetchingModels.value = false
  }
}

async function testEmbedding() {
  testingEmbedding.value = true
  embeddingTestResult.value = null
  try {
    // 先保存当前嵌入配置再测
    await save()
    embeddingTestResult.value = await api.testEmbedding()
    if (embeddingTestResult.value.ok) ElMessage.success(embeddingTestResult.value.message)
    else ElMessage.warning(embeddingTestResult.value.message)
  } catch (e) {
    ElMessage.error('测试失败：' + e.message)
  } finally {
    testingEmbedding.value = false
  }
}

function applyEmbeddingPreset(key) {
  settings.embedding_provider = key
  const preset = embeddingDesc.value?.presets?.[key]
  if (preset) {
    if (preset.base_url) settings.embedding_base_url = preset.base_url
    if (preset.models?.length) settings.embedding_model = preset.models[0]
  }
}

onMounted(load)
</script>

<template>
  <AppShell active="settings">
    <div class="max-w-4xl mx-auto px-3 sm:px-6 py-5 space-y-5">
      <div>
        <h2 class="font-bold text-slate-800">系统设置</h2>
        <p class="text-xs text-slate-500 mt-0.5">模型配置修改后热重载，无需重启服务。未配置时系统进入离线演示模式（内容带明确标识）。</p>
      </div>

      <!-- 生成模型 -->
      <div class="bg-white rounded-xl border border-slate-200 p-5">
        <h3 class="font-bold text-slate-800 mb-4 flex items-center gap-2">
          AI 生成模型
          <el-tag size="small" :type="ai.llmConfigured ? 'success' : 'warning'" effect="light">
            {{ ai.llmConfigured ? '已配置' : '未配置（演示模式）' }}
          </el-tag>
        </h3>

        <!-- 供应商预设 -->
        <div class="flex flex-wrap gap-2 mb-4">
          <el-tag
            v-for="(preset, key) in settings.presets"
            :key="key"
            class="cursor-pointer select-none"
            :type="settings.provider === key ? 'primary' : 'info'"
            :effect="settings.provider === key ? 'dark' : 'plain'"
            @click="applyPreset(key)"
          >
            {{ preset.name }}
          </el-tag>
        </div>

        <div class="grid sm:grid-cols-2 gap-3">
          <el-form-item label="API Key">
            <el-input v-model="settings.api_key" type="password" show-password placeholder="sk-..." />
          </el-form-item>
          <el-form-item label="Base URL">
            <el-input v-model="settings.base_url" placeholder="https://api.deepseek.com/v1" />
          </el-form-item>
          <el-form-item label="模型">
            <div class="flex gap-1.5 w-full">
              <el-select v-model="settings.model" filterable allow-create class="flex-1" placeholder="选择或输入模型名">
                <el-option
                  v-for="m in currentPreset.available_models || []"
                  :key="m"
                  :label="m"
                  :value="m"
                />
              </el-select>
              <el-button :loading="fetchingModels" @click="fetchModels">🌐 联网更新</el-button>
            </div>
          </el-form-item>
          <el-form-item label="温度（创造力）">
            <el-slider v-model="settings.temperature" :min="0" :max="1" :step="0.05" show-input />
          </el-form-item>
          <el-form-item label="单次最大 Token">
            <el-select v-model="settings.max_tokens" class="w-full">
              <el-option v-for="n in [2048, 4096, 8192, 16384]" :key="n" :label="`${n}（${n >= 8192 ? '超长综合大章' : n >= 4096 ? '常规章节' : '简短章节'}）`" :value="n" />
            </el-select>
          </el-form-item>
        </div>

        <div class="flex gap-2 mt-2 flex-wrap">
          <el-button type="primary" :loading="saving" @click="save">保存配置</el-button>
          <el-button :loading="testing" @click="testConnection">测试连通性</el-button>
          <span v-if="testResult" class="text-xs self-center" :class="testResult.ok ? 'text-emerald-600' : 'text-red-600'">
            {{ testResult.message || testResult.error }}
          </span>
        </div>
      </div>

      <!-- 嵌入模型 -->
      <div class="bg-white rounded-xl border border-slate-200 p-5">
        <h3 class="font-bold text-slate-800 mb-1">嵌入检索模型（RAG 语义通道）</h3>
        <p class="text-xs text-slate-500 mb-4">
          独立于生成模型配置。未配置时检索自动退化为 BM25 + LLM 重排（仍可用，语义精度略降）。
          推荐：硅基流动 SiliconFlow 的 BAAI/bge-m3（注册送额度）；或本地 Ollama bge-m3。
        </p>

        <div class="flex flex-wrap gap-2 mb-4">
          <el-tag
            v-for="(preset, key) in embeddingDesc?.presets || {}"
            :key="key"
            class="cursor-pointer select-none"
            :type="settings.embedding_provider === key ? 'primary' : 'info'"
            :effect="settings.embedding_provider === key ? 'dark' : 'plain'"
            @click="applyEmbeddingPreset(key)"
          >
            {{ preset.name }}
          </el-tag>
        </div>

        <div class="grid sm:grid-cols-2 gap-3">
          <el-form-item label="嵌入 API Key（本地 Ollama 可留空）">
            <el-input v-model="settings.embedding_api_key" type="password" show-password placeholder="sk-..." />
          </el-form-item>
          <el-form-item label="嵌入 Base URL">
            <el-input v-model="settings.embedding_base_url" placeholder="https://api.siliconflow.cn/v1" />
          </el-form-item>
          <el-form-item label="嵌入模型">
            <el-select v-model="settings.embedding_model" filterable allow-create class="w-full">
              <el-option
                v-for="m in embeddingDesc?.presets?.[settings.embedding_provider]?.models || []"
                :key="m"
                :label="m"
                :value="m"
              />
            </el-select>
          </el-form-item>
        </div>

        <div class="flex gap-2 mt-2 flex-wrap items-center">
          <el-button :loading="testingEmbedding" @click="testEmbedding">测试嵌入连接</el-button>
          <span v-if="embeddingTestResult" class="text-xs" :class="embeddingTestResult.ok ? 'text-emerald-600' : 'text-amber-600'">
            {{ embeddingTestResult.message }}
          </span>
        </div>
      </div>
    </div>
  </AppShell>
</template>
