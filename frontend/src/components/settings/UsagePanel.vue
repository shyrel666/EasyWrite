<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import api from '@/api/client'
import { callStatusMeta, fillDays, formatMs, formatTokens, purposeLabel } from '@/utils/llmUsage'

/**
 * 模型调用记录：每次真实请求的耗时与用量（离线演示不计入），按日趋势、按用途汇总并列出最近调用。
 */
const GLOBAL = '__global__'
const STATUS_CHIP = { success: 'chip-ok', warning: 'chip-warn', danger: 'chip-bad', info: 'chip-mute' }
const RANGES = [
  { label: '今天', days: 1 },
  { label: '近 7 天', days: 7 },
  { label: '近 30 天', days: 30 },
]

const days = ref(7)
const scope = ref('')
const projects = ref([])
const data = ref(null)
const loading = ref(false)
const error = ref('')

const projectNames = computed(() => Object.fromEntries(projects.value.map((p) => [p.id, p.name])))
const totals = computed(() => data.value?.totals || {})

async function load() {
  loading.value = true
  try {
    const projectId = scope.value === GLOBAL ? '' : scope.value || undefined
    data.value = await api.llmUsage({ days: days.value, projectId })
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function projectLabel(pid) {
  if (!pid) return '未归属项目'
  return projectNames.value[pid] || pid
}

// ---- 每日调用柱状图（单序列，悬停/聚焦看当天明细） ----
const daily = computed(() => (data.value && days.value > 1 ? fillDays(data.value.by_day, days.value) : []))

// 纵轴上限取整，且中线刻度也是整数（次数没有小数）
function niceCeil(n) {
  if (n <= 4) return 4
  const pow = 10 ** Math.floor(Math.log10(n))
  return [1, 2, 4, 6, 8, 10].find((m) => m * pow >= n) * pow
}
const yMax = computed(() => niceCeil(Math.max(0, ...daily.value.map((d) => d.calls))))
const hover = ref(-1)
const hovered = computed(() => daily.value[hover.value] || null)

function dayLabel(day) {
  const [, m, d] = day.split('-')
  return `${Number(m)}月${Number(d)}日`
}
const xTicks = computed(() => {
  const n = daily.value.length
  if (!n) return []
  const idx = n <= 7 ? [...Array(n).keys()] : [0, Math.floor((n - 1) / 2), n - 1]
  return idx.map((i) => ({ i, label: daily.value[i].day.slice(5).replace('-', '/') }))
})

watch([days, scope], load)
onMounted(async () => {
  try {
    projects.value = await api.listProjects()
  } catch { /* 项目列表仅用于筛选 */ }
  load()
})
</script>

<template>
  <div class="space-y-5">
    <!-- 筛选 -->
    <div class="flex items-center gap-2 flex-wrap rise">
      <div class="seg">
        <button v-for="r in RANGES" :key="r.days" class="seg-item" :class="{ 'is-active': days === r.days }" @click="days = r.days">{{ r.label }}</button>
      </div>
      <el-select v-model="scope" class="!w-52" placeholder="全部项目">
        <el-option label="全部项目" value="" />
        <el-option label="未归属项目（知识库等）" :value="GLOBAL" />
        <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
      </el-select>
      <button class="icon-btn ml-auto" title="刷新" :disabled="loading" @click="load">
        <el-icon :class="{ 'animate-spin': loading }"><Refresh /></el-icon>
      </button>
    </div>

    <p v-if="error" class="note note-bad">
      <el-icon class="mt-0.5"><CircleCloseFilled /></el-icon>调用记录加载失败：{{ error }}
    </p>

    <template v-else-if="data">
      <!-- 指标 -->
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-3 rise" style="animation-delay: 40ms">
        <div class="card px-4 py-3.5">
          <p class="text-xs text-ink-3">调用次数</p>
          <p class="text-[22px] font-semibold num mt-1.5 leading-none text-ink">{{ totals.calls }}</p>
          <p class="text-2xs mt-2" :class="totals.errors ? 'text-bad' : 'text-ink-3'">
            失败 <span class="num">{{ totals.errors }}</span> 次
          </p>
        </div>
        <div class="card px-4 py-3.5">
          <p class="text-xs text-ink-3">Token 合计</p>
          <p class="text-[22px] font-semibold num mt-1.5 leading-none text-ink">{{ formatTokens(totals.total_tokens) }}</p>
          <p class="text-2xs text-ink-3 mt-2 num truncate">输入 {{ formatTokens(totals.prompt_tokens) }} · 输出 {{ formatTokens(totals.completion_tokens) }}</p>
        </div>
        <div class="card px-4 py-3.5">
          <p class="text-xs text-ink-3">其中思考 Token</p>
          <p class="text-[22px] font-semibold num mt-1.5 leading-none text-ink">{{ formatTokens(totals.reasoning_tokens) }}</p>
          <p class="text-2xs text-ink-3 mt-2">推理模型的思考过程，计入输出</p>
        </div>
        <div class="card px-4 py-3.5">
          <p class="text-xs text-ink-3">平均耗时</p>
          <p class="text-[22px] font-semibold num mt-1.5 leading-none text-ink">{{ formatMs(totals.avg_latency_ms) }}</p>
          <p class="text-2xs text-ink-3 mt-2 num">P95 {{ formatMs(totals.p95_latency_ms) }}</p>
        </div>
      </div>
      <p v-if="totals.calls_without_usage" class="note note-warn">
        <el-icon class="mt-0.5 text-warn"><WarningFilled /></el-icon>
        {{ totals.calls_without_usage }} 次调用服务商未返回用量（失败或中途取消的请求通常没有），未计入 Token 合计
      </p>

      <!-- 每日调用 -->
      <section v-if="daily.length && totals.calls" class="card px-5 sm:px-6 pt-4 pb-3 rise" style="animation-delay: 80ms">
        <div class="flex items-baseline justify-between gap-3">
          <h3 class="text-[13px] font-semibold text-ink">每日调用次数</h3>
          <p class="text-2xs text-ink-3">悬停柱子查看当天 Token</p>
        </div>
        <div class="relative mt-4 h-36 ml-8">
          <!-- 网格与刻度 -->
          <div class="absolute inset-0 flex flex-col justify-between pointer-events-none" aria-hidden="true">
            <div v-for="t in [yMax, yMax / 2, 0]" :key="t" class="relative border-t border-line" :class="{ '!border-line-strong': t === 0 }">
              <span class="absolute -left-8 -top-2 w-6 text-right text-2xs text-ink-3 num">{{ t }}</span>
            </div>
          </div>
          <!-- 柱 -->
          <div class="absolute inset-0 flex items-end" @mouseleave="hover = -1">
            <div
              v-for="(d, i) in daily"
              :key="d.day"
              class="relative flex-1 h-full flex items-end justify-center px-[1px] outline-none"
              tabindex="0"
              :aria-label="`${dayLabel(d.day)}：${d.calls} 次调用，${formatTokens(d.total_tokens)} Token`"
              @mouseenter="hover = i"
              @focus="hover = i"
              @blur="hover = -1"
            >
              <span
                class="block w-full max-w-[24px] rounded-t-[4px] bg-accent transition-opacity"
                :class="hover >= 0 && hover !== i ? 'opacity-45' : ''"
                :style="{ height: `${(d.calls / yMax) * 100}%` }"
              />
            </div>
          </div>
          <!-- 悬停读数 -->
          <div
            v-if="hovered"
            class="absolute -top-2 z-10 -translate-x-1/2 -translate-y-full pointer-events-none rounded-lg border border-line bg-surface shadow-float px-2.5 py-1.5 whitespace-nowrap"
            :style="{ left: `${((hover + 0.5) / daily.length) * 100}%` }"
          >
            <p class="text-2xs text-ink-3">{{ dayLabel(hovered.day) }}</p>
            <p class="text-xs text-ink mt-0.5"><b class="num font-semibold">{{ hovered.calls }}</b> 次 · <span class="num">{{ formatTokens(hovered.total_tokens) }}</span> Token</p>
          </div>
        </div>
        <div class="relative h-5 ml-8 mt-1.5 text-2xs text-ink-3 num" aria-hidden="true">
          <span
            v-for="t in xTicks"
            :key="t.i"
            class="absolute -translate-x-1/2"
            :style="{ left: `${((t.i + 0.5) / daily.length) * 100}%` }"
          >{{ t.label }}</span>
        </div>
      </section>

      <div v-if="!totals.calls" class="card py-14 text-center rise" style="animation-delay: 80ms">
        <el-icon :size="22" class="text-ink-3"><Histogram /></el-icon>
        <p class="mt-2 text-sm text-ink-2">该时间范围内没有模型调用</p>
        <p class="hint mt-1">配置生成模型后，拆标、撰写等每次请求都会记在这里</p>
      </div>
      <template v-else>
        <section class="card overflow-hidden rise" style="animation-delay: 120ms">
          <div class="px-5 sm:px-6 py-3.5 border-b border-line">
            <h3 class="text-[13px] font-semibold text-ink">按用途</h3>
          </div>
          <el-table :data="data.by_purpose" size="small">
            <el-table-column label="用途" min-width="120">
              <template #default="{ row }"><span class="pl-2">{{ purposeLabel(row.purpose) }}</span></template>
            </el-table-column>
            <el-table-column prop="calls" label="次数" width="80" align="right" />
            <el-table-column label="失败" width="80" align="right">
              <template #default="{ row }"><span :class="row.errors ? 'text-bad' : 'text-ink-3'">{{ row.errors }}</span></template>
            </el-table-column>
            <el-table-column label="Token" width="110" align="right">
              <template #default="{ row }"><span class="num">{{ formatTokens(row.total_tokens) }}</span></template>
            </el-table-column>
            <el-table-column label="平均耗时" width="110" align="right">
              <template #default="{ row }"><span class="num pr-2">{{ formatMs(row.avg_latency_ms) }}</span></template>
            </el-table-column>
          </el-table>
        </section>

        <section class="card overflow-hidden rise" style="animation-delay: 160ms">
          <div class="px-5 sm:px-6 py-3.5 border-b border-line flex items-baseline justify-between gap-3">
            <h3 class="text-[13px] font-semibold text-ink">最近调用</h3>
            <p class="text-2xs text-ink-3">悬停「失败」可看错误原因</p>
          </div>
          <el-table :data="data.recent" size="small" max-height="420">
            <el-table-column label="时间" width="160">
              <template #default="{ row }"><span class="pl-2 text-ink-3 num">{{ row.created_at }}</span></template>
            </el-table-column>
            <el-table-column label="用途" min-width="130">
              <template #default="{ row }">
                <div>{{ purposeLabel(row.purpose) }}</div>
                <div class="text-2xs text-ink-3 truncate" :title="projectLabel(row.project_id)">{{ projectLabel(row.project_id) }}</div>
              </template>
            </el-table-column>
            <el-table-column label="模型" min-width="120">
              <template #default="{ row }"><span class="text-xs font-mono text-ink-2">{{ row.model }}</span></template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tooltip v-if="row.error" :content="row.error" placement="top">
                  <span class="chip cursor-help" :class="STATUS_CHIP[callStatusMeta(row.status).type]">{{ callStatusMeta(row.status).label }}</span>
                </el-tooltip>
                <span v-else class="chip" :class="STATUS_CHIP[callStatusMeta(row.status).type]">{{ callStatusMeta(row.status).label }}</span>
              </template>
            </el-table-column>
            <el-table-column label="耗时" width="100" align="right">
              <template #default="{ row }">
                <div class="num">{{ formatMs(row.latency_ms) }}</div>
                <div v-if="row.first_token_ms != null" class="text-2xs text-ink-3 num">首字 {{ formatMs(row.first_token_ms) }}</div>
              </template>
            </el-table-column>
            <el-table-column label="Token（入/出）" width="130" align="right">
              <template #default="{ row }">
                <span v-if="row.total_tokens == null" class="text-ink-3 pr-2">—</span>
                <span v-else class="num pr-2">{{ formatTokens(row.prompt_tokens) }} / {{ formatTokens(row.completion_tokens) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </section>
      </template>
    </template>
    <div v-else class="space-y-3">
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div v-for="i in 4" :key="i" class="h-[92px] rounded-xl skeleton" />
      </div>
      <div class="h-48 rounded-xl skeleton" />
    </div>
  </div>
</template>
