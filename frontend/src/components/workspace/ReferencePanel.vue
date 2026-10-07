<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useProjectStore } from '@/stores/project'

const props = defineProps({
  node: { type: Object, default: null },
  projectId: { type: String, required: true },
})
const emit = defineEmits(['refs-changed', 'close'])

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
// 面板可能在项目加载后才打开（窄屏默认收起），挂载时自行载入全局事实
onMounted(open)

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
    const cls = s >= 8 ? 'chip-ok' : s >= 6 ? 'chip-warn' : 'chip-mute'
    return { text: `重排 ${s}`, cls }
  }
  return { text: '召回', cls: 'chip-mute' }
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
  <div class="h-full flex flex-col bg-surface">
    <div class="shrink-0 h-14 flex items-center gap-2 px-3 border-b border-line">
      <div class="seg flex-1">
        <button
          v-for="t in [
            { k: 'refs', l: '知识参考', n: node?.last_refs?.length || 0 },
            { k: 'facts', l: '全局事实' },
            { k: 'instr', l: '写作意见' },
          ]"
          :key="t.k"
          class="seg-item flex-1"
          :class="{ 'is-active': activeTab === t.k }"
          @click="activeTab = t.k"
        >
          {{ t.l }}<span v-if="t.n" class="ml-1 text-ink-3 num">{{ t.n }}</span>
        </button>
      </div>
      <button class="icon-btn shrink-0" title="收起" @click="emit('close')"><el-icon><Close /></el-icon></button>
    </div>

    <div class="flex-1 overflow-auto p-3 space-y-2.5">
      <!-- 引用溯源 -->
      <template v-if="activeTab === 'refs'">
        <div v-if="!node?.last_refs?.length" class="text-xs text-ink-3 py-12 px-4 text-center leading-relaxed">
          <el-icon :size="22" class="text-line-strong mb-2"><Collection /></el-icon>
          <p>撰写本节后，这里会列出知识库检索命中的历史参考。</p>
          <p class="mt-1">可以「锁定」优先注入，或「排除」不再使用。</p>
        </div>
        <article
          v-for="refItem in node?.last_refs || []"
          :key="refItem.chunk_id"
          class="rounded-lg border p-3 text-xs transition"
          :class="[
            isPinned(refItem) ? 'border-accent/50 bg-accent-soft/40' : 'border-line hover:border-line-strong',
            isExcluded(refItem) ? 'opacity-45' : '',
          ]"
        >
          <div class="flex items-start justify-between gap-2">
            <p class="font-medium text-ink leading-snug line-clamp-2 flex-1">{{ refItem.section_title || refItem.breadcrumb }}</p>
            <span class="chip shrink-0" :class="scoreLabel(refItem).cls">{{ scoreLabel(refItem).text }}</span>
          </div>
          <p class="text-2xs text-ink-3 truncate mt-1" :title="`${refItem.doc_name} / ${refItem.breadcrumb}`">
            {{ refItem.doc_name }} / {{ refItem.breadcrumb }}
          </p>
          <p class="text-ink-2 leading-relaxed line-clamp-4 mt-2">{{ refItem.content }}</p>
          <p v-if="refItem.rerank_reason" class="text-2xs text-accent-fg mt-2 leading-relaxed">评审：{{ refItem.rerank_reason }}</p>
          <div class="flex items-center gap-1 mt-2.5 -mb-0.5">
            <el-button
              size="small"
              :type="isPinned(refItem) ? 'primary' : 'default'"
              :plain="!isPinned(refItem)"
              @click="togglePin(refItem)"
            >
              <el-icon class="mr-1"><Paperclip /></el-icon>{{ isPinned(refItem) ? '已锁定' : '锁定' }}
            </el-button>
            <el-button v-if="!isExcluded(refItem)" size="small" text @click="exclude(refItem)">排除</el-button>
            <span v-else class="text-2xs text-ink-3 ml-1">已排除</span>
          </div>
        </article>
      </template>

      <!-- 全局事实 -->
      <template v-else-if="activeTab === 'facts'">
        <div v-if="factsDraft" class="space-y-3 text-sm p-1">
          <p class="note note-info">
            <el-icon class="mt-0.5 text-accent-fg shrink-0"><Lock /></el-icon>
            <span>作为只读硬约束注入每次生成，保证全书公司名、产品与技术路线前后一致。留空即不编造。</span>
          </p>
          <div v-for="f in [
            { k: 'company_name', l: '投标企业全称' },
            { k: 'core_product_name', l: '核心产品 / 平台' },
            { k: 'architecture_stack', l: '技术架构路线' },
            { k: 'database_selection', l: '数据库底座' },
            { k: 'sla_commitment', l: 'SLA 售后承诺' },
            { k: 'delivery_guarantee', l: '工期交付承诺' },
          ]" :key="f.k">
            <label class="field-label">{{ f.l }}</label>
            <el-input v-model="factsDraft[f.k]" placeholder="留空 = 不编造" />
          </div>
          <div>
            <label class="field-label">未提及事实的处理</label>
            <el-select v-model="factsDraft.fact_completeness_mode" class="w-full">
              <el-option label="【待填写】占位" value="placeholder" />
              <el-option label="保持模糊表述" value="omit" />
            </el-select>
          </div>
          <el-button type="primary" class="w-full" :loading="savingFacts" @click="saveFacts">保存全局事实</el-button>
        </div>
      </template>

      <!-- 写作意见 -->
      <template v-else>
        <div class="p-1 space-y-2">
          <label class="field-label">本节额外的写作要求</label>
          <el-input
            :model-value="instruction"
            type="textarea"
            :rows="8"
            placeholder="如：重点强调国产化适配；给出详细的验收测试用例；篇幅控制在 3000 字以内"
            @input="instruction = $event; saveInstruction()"
          />
          <p class="hint">意见作为本节的额外指导注入生成提示词，随项目保存在章节要求中。</p>
        </div>
      </template>
    </div>
  </div>
</template>
