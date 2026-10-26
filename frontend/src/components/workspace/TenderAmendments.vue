<script setup>
import { computed, ref } from 'vue'
import api from '@/api/client'
const props = defineProps({ projectId: { type: String, required: true } })
const emit = defineEmits(['changed'])
const state = ref({ documents: [], overrides: [], clauses: [], package: '' })
const error = ref(''), busy = ref(false)
const doc = ref({ name: '', kind: 'addendum', published_at: '', text: '' })
const draft = ref({ document_id: '', clause_id: '', action: 'replace', text: '', source_excerpt: '', source_page: 1 })
const selected = computed(() => state.value.clauses.find(c => c.id === draft.value.clause_id))
async function run(fn) {
  busy.value = true; error.value = ''
  try { await fn() } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function load() { state.value = await api.tenderVersions(props.projectId) }
async function addDocument() { await run(async () => { await api.addTenderDocument(props.projectId, { ...doc.value, package: state.value.package }); await load() }) }
async function upload(event) {
  const file = event.target.files?.[0]
  if (file) await run(async () => { await api.uploadAddendum(props.projectId, file, { ...doc.value, package: state.value.package }); await load() })
}
async function propose() {
  await run(async () => {
    await api.suggestOverride(props.projectId, { ...draft.value, package: state.value.package, basis_hash: selected.value.basis_hash })
    await load()
  })
}
async function decide(row, decision) {
  await run(async () => { await api.decideOverride(props.projectId, row.id, { state: decision, basis_hash: row.basis_hash }); await load(); emit('changed') })
}
</script>

<template>
  <details class="border border-line rounded-lg p-3 mb-3" @toggle="e => e.target.open && run(load)">
    <summary class="cursor-pointer font-medium">补遗 / 澄清版本与条款覆盖</summary>
    <p class="hint my-2">当前包：{{ state.package || '单包' }}。先登记文件，再对照条款确认；同日文件不自动决定优先级。</p>
    <p v-if="error" role="alert" class="text-danger">{{ error }}</p>
    <fieldset :disabled="busy" class="space-y-2">
      <input v-model="doc.name" aria-label="文件名称" placeholder="补遗名称" class="input-base w-full" />
      <select v-model="doc.kind" aria-label="文件类型"><option value="addendum">补遗</option><option value="clarification">澄清</option></select>
      <input v-model="doc.published_at" type="date" aria-label="发布日期" />
      <input type="file" accept=".docx,.pdf" aria-label="上传补遗" :disabled="!doc.published_at" @change="upload" />
      <textarea v-model="doc.text" aria-label="补遗正文" placeholder="也可粘贴补遗原文" class="input-base w-full" />
      <button class="btn-secondary" :disabled="!doc.name || !doc.text || !doc.published_at" @click="addDocument">登记文本版本</button>
      <details v-for="d in state.documents" :key="d.id"><summary>V{{ d.version }} {{ d.name }} · {{ d.published_at || '原件' }}</summary><p class="hint break-all">SHA256: {{ d.sha256 }}</p><pre class="whitespace-pre-wrap max-h-48 overflow-auto">{{ d.text }}</pre></details>
      <select v-model="draft.document_id" aria-label="来源文件" class="w-full"><option value="">选择补遗或澄清</option><option v-for="d in state.documents.filter(d => d.kind !== 'original')" :key="d.id" :value="d.id">V{{ d.version }} {{ d.name }}</option></select>
      <select v-model="draft.clause_id" aria-label="原条款" class="w-full"><option value="">选择原条款</option><option v-for="c in state.clauses" :key="c.id" :value="c.id">{{ c.label }}：{{ c.text.slice(0,60) }}</option></select>
      <button v-for="(s, i) in state.suggestions || []" :key="i" class="btn-secondary" @click="draft = { ...s }">使用规则建议（仍须核对页码并确认）：{{ s.text }}</button>
      <div v-if="selected"><p>原文：{{ selected.original_text }}</p><p>当前有效：{{ selected.text || '已撤销' }}</p><p>影响章节：{{ selected.sections.map(s => s.title).join('、') || '尚无章节' }}</p></div>
      <label v-if="selected?.scoring_item_id">新分值（留空保留）<input :value="draft.points" type="number" min="0" aria-label="新分值" @input="draft.points = $event.target.value === '' ? null : Number($event.target.value)" /></label>
      <select v-model="draft.action" aria-label="覆盖方式"><option value="replace">替换</option><option value="append">追加</option><option value="revoke">撤销条款</option></select>
      <textarea v-model="draft.text" aria-label="替代文本" placeholder="明确的替代或追加文本" class="input-base w-full" />
      <textarea v-model="draft.source_excerpt" aria-label="来源摘录" placeholder="逐字摘录补遗中的依据" class="input-base w-full" />
      <label>来源页 <input v-model.number="draft.source_page" type="number" min="1" class="w-16" /></label>
      <button class="btn-secondary" :disabled="!selected || !draft.document_id || !draft.source_excerpt" @click="propose">保存待确认覆盖</button>
      <article v-for="row in state.overrides" :key="row.id" class="border-t border-line py-2">
        <p>{{ row.state === 'pending' ? '待确认（尚未生效）' : row.state === 'confirmed' ? '已确认生效' : '已撤回' }} · 第 {{ row.source_page }} 页</p>
        <p>原文：{{ row.original_text }}</p><p>变更：{{ row.action === 'revoke' ? '撤销条款' : row.text }}</p>
        <button v-if="row.state === 'pending'" class="btn-primary" @click="decide(row, 'confirmed')">核对后确认生效</button>
        <button v-if="row.state !== 'withdrawn'" class="btn-secondary" @click="decide(row, 'withdrawn')">撤回</button>
      </article>
    </fieldset>
  </details>
</template>
