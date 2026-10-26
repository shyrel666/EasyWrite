<script setup>
import { computed, ref } from 'vue'
import api from '@/api/client'
const props = defineProps({ projectId: String, items: { type: Array, default: () => [] } })
const emit = defineEmits(['locate', 'changed'])
const notes = ref({})
const error = ref('')
const saving = ref(false)
const occurrences = computed(() => props.items.reduce((n, v) => n + (v.occurrences?.length || 1), 0))
async function confirm(item) {
  saving.value = true
  error.value = ''
  try {
    await api.confirmVerification(props.projectId, item.stable_key, { basis_hash: item.basis_hash, note: notes.value[item.stable_key] })
    emit('changed')
  } catch (e) { error.value = e.message }
  finally { saving.value = false }
}
</script>

<template>
  <details v-if="items.length" class="text-xs border border-line rounded p-3 mt-3">
    <summary class="cursor-pointer">待核实 {{ items.length }} 项 / {{ occurrences }} 处</summary>
    <p class="hint mt-2">核实结论绑定当前依据；资料、承诺变更后失效。结论不能替代企业资料确认或正文补齐。</p>
    <p v-if="error" class="text-bad">{{ error }}</p>
    <div v-for="v in items" :key="v.stable_key" class="border-t border-line mt-2 pt-2 space-y-1">
      <p>{{ v.note || v.text }}</p>
      <p v-if="v.decision_state === 'stale'" class="text-warn">依据已变化，原核实结论失效</p>
      <p v-else-if="v.decision_state === 'confirmed'" class="text-ok">核实结论：{{ v.decision_note }}</p>
      <ul><li v-for="(o, k) in v.occurrences" :key="k"><button type="button" class="text-accent-fg text-left" @click="emit('locate', o.section_id)">{{ o.section_title }} 第 {{ o.line }} 行：{{ o.text }}</button></li></ul>
      <input v-model="notes[v.stable_key]" aria-label="核实结论及依据" placeholder="核实结论及依据" class="bg-surface border border-line rounded px-2 py-1 w-full" />
      <button type="button" class="text-accent-fg" :disabled="saving || !notes[v.stable_key]?.trim()" @click="confirm(v)">记录核实结论</button>
    </div>
  </details>
</template>
