<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { materialChip } from '@/utils/material'

// 为一个评分项关联证明资料：建议只作提示，勾选并保存后才写入项目
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  projectId: { type: String, required: true },
  // evidence 报告中的评分项：{ item_id, name, points, criteria, note, links, missing_links }
  item: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'saved'])

const KIND_LABEL = { qualifications: '资质', personnel: '人员', cases: '业绩' }
const loading = ref(false)
const saving = ref(false)
const suggestions = ref([])
const catalog = ref([])
const selected = ref(new Set())
const keyword = ref('')

const keyOf = (c) => `${c.kind}:${c.asset_id}`

watch(
  () => props.modelValue,
  async (open) => {
    if (!open || !props.item) return
    selected.value = new Set((props.item.links || []).map(keyOf))
    keyword.value = ''
    loading.value = true
    try {
      const [sug, check] = await Promise.all([
        api.evidenceSuggestions(props.projectId),
        api.assets.materialCheck(props.projectId),
      ])
      suggestions.value = sug.suggestions[props.item.item_id] || []
      // 预设示例不能作为证明材料，不列出
      catalog.value = check.checks.filter((c) => c.asset_status !== 'example')
    } catch (e) {
      ElMessage.error('资料加载失败：' + e.message)
    } finally {
      loading.value = false
    }
  }
)

const checkByKey = computed(() => Object.fromEntries(catalog.value.map((c) => [keyOf(c), c])))
const suggestedKeys = computed(() => new Set(suggestions.value.map((s) => s.key)))
const others = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return catalog.value.filter((c) => !suggestedKeys.value.has(keyOf(c)) && (!kw || c.name.toLowerCase().includes(kw)))
})
const groups = computed(() =>
  Object.keys(KIND_LABEL)
    .map((kind) => ({ kind, label: KIND_LABEL[kind], rows: others.value.filter((c) => c.kind === kind) }))
    .filter((g) => g.rows.length)
)

function toggle(key) {
  const next = new Set(selected.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  selected.value = next
}

function takeSuggestions() {
  selected.value = new Set([...selected.value, ...suggestions.value.map((s) => s.key)])
}

async function save() {
  saving.value = true
  try {
    // 只保存仍存在的资料：已删除资料的关联随保存移除
    const keys = [...selected.value].filter((k) => checkByKey.value[k])
    await api.linkEvidence(props.projectId, props.item.item_id, keys)
    ElMessage.success(keys.length ? `已关联 ${keys.length} 项资料` : '已取消关联')
    emit('saved')
    emit('update:modelValue', false)
  } catch (e) {
    ElMessage.error('保存失败：' + e.message)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="item ? `关联证明材料 · ${item.name}` : '关联证明材料'"
    width="min(640px, 94vw)"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <template v-if="item">
      <div class="rounded-lg bg-sunken px-3.5 py-3 text-xs leading-relaxed text-ink-2 -mt-2 mb-4 max-h-36 overflow-auto">
        <p><span class="font-medium text-ink">评分标准</span><span v-if="item.points != null" class="num"> · {{ item.points }} 分</span>：{{ item.criteria || '—' }}</p>
        <p v-if="item.note" class="mt-1.5"><span class="font-medium text-ink">证明材料要求</span>：{{ item.note }}</p>
      </div>

      <div v-if="loading" class="py-10 text-center text-sm text-ink-3">加载资料…</div>
      <template v-else>
        <p v-if="item.missing_links?.length" class="note note-warn mb-3 text-xs">
          {{ item.missing_links.length }} 条已关联的资料已被删除，保存后移除。
        </p>

        <section v-if="suggestions.length" class="mb-4">
          <div class="flex items-center justify-between mb-2">
            <h4 class="text-[13px] font-semibold text-ink">建议关联 <span class="hint font-normal">按评分标准与资料名称匹配，请逐条确认</span></h4>
            <el-button size="small" text type="primary" @click="takeSuggestions">勾选全部建议</el-button>
          </div>
          <div
            v-for="s in suggestions"
            :key="s.key"
            role="checkbox"
            :aria-checked="selected.has(s.key)"
            tabindex="0"
            class="flex items-start gap-2.5 rounded-lg border px-3 py-2.5 mb-1.5 cursor-pointer transition"
            :class="selected.has(s.key) ? 'border-accent/50 bg-accent-soft/40' : 'border-line hover:border-line-strong'"
            @click="toggle(s.key)"
            @keydown.space.prevent="toggle(s.key)"
          >
            <el-checkbox :model-value="selected.has(s.key)" class="!h-5 pointer-events-none" tabindex="-1" />
            <span class="min-w-0 flex-1">
              <span class="flex items-center gap-1.5 flex-wrap">
                <span class="chip chip-mute">{{ KIND_LABEL[s.kind] }}</span>
                <span class="text-sm text-ink">{{ s.name }}</span>
                <span v-if="checkByKey[s.key]" class="chip" :class="materialChip(checkByKey[s.key].status)">{{ checkByKey[s.key].status }}</span>
              </span>
              <span class="block text-2xs text-ink-3 mt-1">{{ s.reason }}</span>
            </span>
          </div>
        </section>

        <section>
          <div class="flex items-center justify-between gap-3 mb-2">
            <h4 class="text-[13px] font-semibold text-ink shrink-0">{{ suggestions.length ? '其他资料' : '企业资料' }}</h4>
            <el-input v-model="keyword" size="small" clearable placeholder="按名称筛选" class="!w-48" />
          </div>
          <p v-if="!catalog.length" class="text-sm text-ink-3 py-6 text-center">
            资料库中还没有可用的资质、人员或业绩（示例资料不能作为证明材料），请先到「企业资产」录入。
          </p>
          <p v-else-if="!groups.length" class="text-xs text-ink-3 py-4 text-center">没有其他匹配的资料</p>
          <div v-for="g in groups" :key="g.kind" class="mb-3">
            <p class="text-2xs font-semibold tracking-wider text-ink-3 mb-1">{{ g.label }}</p>
            <div
              v-for="c in g.rows"
              :key="keyOf(c)"
              role="checkbox"
              :aria-checked="selected.has(keyOf(c))"
              tabindex="0"
              class="flex items-center gap-2.5 rounded-md px-2 py-1.5 cursor-pointer hover:bg-sunken"
              @click="toggle(keyOf(c))"
              @keydown.space.prevent="toggle(keyOf(c))"
            >
              <el-checkbox :model-value="selected.has(keyOf(c))" class="!h-5 pointer-events-none" tabindex="-1" />
              <span class="text-sm text-ink truncate flex-1 min-w-0" :title="c.name">{{ c.name }}</span>
              <span class="chip shrink-0" :class="materialChip(c.status)" :title="c.problems.map((p) => p.message).join('；')">{{ c.status }}</span>
            </div>
          </div>
        </section>
      </template>
    </template>
    <template #footer>
      <span class="text-xs text-ink-3 mr-auto float-left leading-8">已选 <span class="num">{{ selected.size }}</span> 项</span>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" :disabled="loading" @click="save">保存关联</el-button>
    </template>
  </el-dialog>
</template>
