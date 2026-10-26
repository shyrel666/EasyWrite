<script setup>
const props = defineProps({ modelValue: { type: Array, default: () => [] }, requirements: { type: Array, default: () => [] } })
const emit = defineEmits(['update:modelValue'])
const topics = { response: '响应时限', onsite: '到场时限', resolve: '修复时限', duration: '工期', warranty: '质保', staff: '驻场人数', inspection: '巡检', window: '服务时间' }
function update(id, field, value) {
  emit('update:modelValue', props.modelValue.map(c => c.id === id ? { ...c, [field]: value, confirmed: field === 'confirmed' ? value : false } : c))
}
function add(requirement) {
  const row = requirement ? { ...requirement, confirmed: false } : {
    id: crypto.randomUUID(), topic: 'response', value: '', unit: '小时', comparator: 'eq', severity: '', scope: '', subject: '', source: '人工录入', confirmed: false,
  }
  emit('update:modelValue', [...props.modelValue, row])
}
function remove(id) { emit('update:modelValue', props.modelValue.filter(c => c.id !== id)) }
</script>

<template>
  <div class="card p-4 space-y-3">
    <h3 class="font-semibold text-sm">分级承诺</h3>
    <p class="hint">采购要求单独保存；请核对企业能力后确认。修改任一字段后需要重新确认，并保存全局事实。</p>
    <div v-for="r in requirements.filter(r => !modelValue.some(c => c.id === r.id))" :key="r.id" class="note note-info flex justify-between gap-2">
      <span>采购要求：{{ r.subject }} {{ r.scope }} {{ r.severity }} {{ topics[r.topic] }} {{ r.value }}{{ r.unit }}</span>
      <button type="button" class="text-accent-fg shrink-0" @click="add(r)">加入待确认</button>
    </div>
    <div v-for="c in modelValue" :key="c.id" class="border border-line rounded p-3 space-y-2" :data-commitment="c.id">
      <div class="grid grid-cols-2 gap-2">
        <label class="field-label">类别<select :value="c.topic" class="w-full bg-surface" @change="update(c.id, 'topic', $event.target.value)"><option v-for="(label, value) in topics" :key="value" :value="value">{{ label }}</option></select></label>
        <label class="field-label">比较方向<select :value="c.comparator" class="w-full bg-surface" @change="update(c.id, 'comparator', $event.target.value)"><option value="eq">等于</option><option value="le">不超过</option><option value="ge">不少于</option></select></label>
        <label v-for="(label, field) in { value: '数值', unit: '单位', severity: '级别（如重大故障）', scope: '场景', subject: '主体', source: '来源' }" :key="field" class="field-label">{{ label }}<input :aria-label="label" :value="c[field]" class="w-full bg-surface border border-line rounded px-2 py-1" @input="update(c.id, field, $event.target.value)" /></label>
      </div>
      <div class="flex gap-3 items-center">
        <label class="text-sm"><input type="checkbox" :checked="c.confirmed" :disabled="!c.value.trim() || !c.source.trim()" @change="update(c.id, 'confirmed', $event.target.checked)" /> 企业已确认</label>
        <button type="button" class="text-bad text-xs" @click="remove(c.id)">拒绝 / 移除</button>
      </div>
    </div>
    <button type="button" class="text-accent-fg text-sm" @click="add()">添加承诺</button>
  </div>
</template>
