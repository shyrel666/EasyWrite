<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useProjectStore } from '@/stores/project'

const props = defineProps({
  node: { type: Object, default: null },
  projectId: { type: String, required: true },
})
const emit = defineEmits(['refs-changed'])

const store = useProjectStore()
const activeTab = ref('refs')
const factsDraft = ref(null)
const savingFacts = ref(false)
const instruction = ref('')
const instructionTimer = ref(null)

function open(node) {
  factsDraft.value = { ...(store.facts || {}), custom_facts: { ...(store.facts?.custom_facts || {}) } }
}

defineExpose({ open })

async function saveFacts() {
  savingFacts.value = true
  try {
    await store.saveFacts(factsDraft.value)
    ElMessage.success('全局事实已保存（后续生成立即生效）')
  } catch (e) {
    ElMessage.error('保存失败：' + e.message)
  } finally {
    savingFacts.value = false
  }
}

function isPinned(refItem) {
  return (props.node?.pinned_refs || []).includes(refItem.chunk_id)
}
function isExcluded(refItem) {
  return (props.node?.excluded_refs || []).includes(refItem.chunk_id)
}

async function togglePin(refItem) {
  if (!props.node) return
  const pinned = [...(props.node.pinned_refs || [])]
  const excluded = [...(props.node.excluded_refs || [])]
  const i = pinned.indexOf(refItem.chunk_id)
  if (i >= 0) pinned.splice(i, 1)
  else {
    pinned.push(refItem.chunk_id)
    const j = excluded.indexOf(refItem.chunk_id)
    if (j >= 0) excluded.splice(j, 1)
  }
  try {
    await api.updateSectionRefs(props.projectId, props.node.id, pinned, excluded)
    props.node.pinned_refs = pinned
    props.node.excluded_refs = excluded
    ElMessage.success(isPinned(refItem) ? '已锁定（重新生成时必注入）' : '已取消锁定')
    emit('refs-changed')
  } catch (e) {
    ElMessage.error('操作失败：' + e.message)
  }
}

async function exclude(refItem) {
  if (!props.node) return
  const pinned = [...(props.node.pinned_refs || [])]
  const excluded = [...(props.node.excluded_refs || [])]
  const i = pinned.indexOf(refItem.chunk_id)
  if (i >= 0) pinned.splice(i, 1)
  if (!excluded.includes(refItem.chunk_id)) excluded.push(refItem.chunk_id)
  try {
    await api.updateSectionRefs(props.projectId, props.node.id, pinned, excluded)
    props.node.pinned_refs = pinned
    props.node.excluded_refs = excluded
    ElMessage.success('已排除该参考（重新生成时不再注入）')
    emit('refs-changed')
  } catch (e) {
    ElMessage.error('操作失败：' + e.message)
  }
}

function scoreLabel(refItem) {
  if (refItem.rerank_score != null) {
    const s = refItem.rerank_score
    const cls = s >= 8 ? 'bg-emerald-100 text-emerald-700' : s >= 6 ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-500'
    return { text: `重排 ${s}分`, cls }
  }
  return { text: '召回', cls: 'bg-slate-100 text-slate-500' }
}

function saveInstruction() {
  if (instructionTimer.value) clearTimeout(instructionTimer.value)
  instructionTimer.value = setTimeout(() => {
    if (props.node) {
      props.node.requirements = instruction.value ? [instruction.value] : []
    }
  }, 600)
}
</script>

<template>
  <div class="h-full flex flex-col bg-white">
    <!-- 子标签 -->
    <div class="flex border-b border-slate-200 shrink-0 text-sm">
      <button
        v-for="t in [
          { k: 'refs', l: `知识参考 ${node?.last_refs?.length ? `(${node.last_refs.length})` : ''}` },
          { k: 'facts', l: '全局事实' },
          { k: 'instr', l: '专家意见' },
        ]"
        :key="t.k"
        class="flex-1 py-2 text-center border-b-2 transition"
        :class="activeTab === t.k ? 'border-blue-500 text-blue-600 font-medium' : 'border-transparent text-slate-500'"
        @click="activeTab = t.k"
      >
        {{ t.l }}
      </button>
    </div>

    <div class="flex-1 overflow-auto p-3 space-y-2.5">
      <!-- 引用溯源 -->
      <template v-if="activeTab === 'refs'">
        <div v-if="!node?.last_refs?.length" class="text-xs text-slate-400 py-8 text-center leading-relaxed">
          撰写章节后将在此展示知识库检索到的历史参考<br />
          可【锁定】优先注入或【排除】不再使用
        </div>
        <div
          v-for="refItem in node?.last_refs || []"
          :key="refItem.chunk_id"
          class="border border-slate-200 rounded-lg p-2.5 text-xs hover:border-blue-300 transition"
          :class="isExcluded(refItem) ? 'opacity-40' : ''"
        >
          <div class="flex items-center justify-between gap-2 mb-1">
            <span class="font-medium text-slate-700 truncate flex-1">{{ refItem.section_title || refItem.breadcrumb }}</span>
            <span class="px-1.5 py-0.5 rounded text-[10px] shrink-0" :class="scoreLabel(refItem).cls">
              {{ scoreLabel(refItem).text }}
            </span>
          </div>
          <p class="text-slate-400 truncate mb-1.5">{{ refItem.doc_name }} / {{ refItem.breadcrumb }}</p>
          <p class="text-slate-600 leading-relaxed line-clamp-3">{{ refItem.content }}</p>
          <div v-if="refItem.rerank_reason" class="text-[11px] text-blue-500 mt-1">评审：{{ refItem.rerank_reason }}</div>
          <div class="flex gap-2 mt-2">
            <el-button
              size="small"
              :type="isPinned(refItem) ? 'primary' : 'default'"
              :plain="!isPinned(refItem)"
              @click="togglePin(refItem)"
            >
              {{ isPinned(refItem) ? '✓ 已锁定' : '锁定' }}
            </el-button>
            <el-button v-if="!isExcluded(refItem)" size="small" text type="danger" @click="exclude(refItem)">排除</el-button>
          </div>
        </div>
      </template>

      <!-- 全局事实 -->
      <template v-else-if="activeTab === 'facts'">
        <div v-if="factsDraft" class="space-y-2.5 text-sm">
          <div v-for="f in [
            { k: 'company_name', l: '投标企业全称' },
            { k: 'core_product_name', l: '核心产品/平台' },
            { k: 'architecture_stack', l: '技术架构路线' },
            { k: 'database_selection', l: '数据库底座' },
            { k: 'sla_commitment', l: 'SLA 售后承诺' },
            { k: 'delivery_guarantee', l: '工期交付承诺' },
          ]" :key="f.k">
            <label class="text-xs text-slate-500 block mb-0.5">{{ f.l }}</label>
            <el-input v-model="factsDraft[f.k]" size="small" placeholder="留空 = 不编造" />
          </div>
          <div>
            <label class="text-xs text-slate-500 block mb-0.5">未提及事实的完备纪律</label>
            <el-select v-model="factsDraft.fact_completeness_mode" size="small" class="w-full">
              <el-option label="【待填写】占位" value="placeholder" />
              <el-option label="保持模糊" value="omit" />
            </el-select>
          </div>
          <el-button type="primary" size="small" class="w-full" :loading="savingFacts" @click="saveFacts">
            保存全局事实
          </el-button>
          <p class="text-[11px] text-slate-400 leading-relaxed">
            全局事实作为只读硬约束注入每次生成，确保全书公司名、产品、技术路线前后一致。
          </p>
        </div>
      </template>

      <!-- 专家意见 -->
      <template v-else>
        <el-input
          :model-value="instruction"
          type="textarea"
          :rows="6"
          placeholder="补充本章节的特殊写作要求，如：重点强调国产化适配、提供详细的验收测试用例、控制篇幅不超过3000字等"
          @input="instruction = $event; saveInstruction()"
        />
        <p class="text-[11px] text-slate-400 leading-relaxed">
          专家意见将作为本章节的额外指导注入生成提示词。意见内容随项目保存在章节 requirements 中。
        </p>
      </template>
    </div>
  </div>
</template>
