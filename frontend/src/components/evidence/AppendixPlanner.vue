<script setup>
const props = defineProps({ options: { type: Array, default: () => [] }, modelValue: { type: Array, default: () => [] } })
const emit = defineEmits(['update:modelValue'])
const key = r => `${r.asset_key}:${r.attachment_id}`
function select(option, checked) {
  emit('update:modelValue', checked ? [...props.modelValue, { ...option, pages: [] }] : props.modelValue.filter(r => key(r) !== key(option)))
}
function update(index, field, value) { emit('update:modelValue', props.modelValue.map((r, i) => i === index ? { ...r, [field]: value } : r)) }
function move(index, step) {
  const rows = [...props.modelValue]
  ;[rows[index], rows[index + step]] = [rows[index + step], rows[index]]
  emit('update:modelValue', rows)
}
</script>
<template>
  <details v-if="options.length" class="text-xs mb-3 border border-line rounded p-3">
    <summary class="cursor-pointer">证明附件编排（已选 {{ modelValue.length }} 份）</summary>
    <p class="hint my-2">仅使用企业资料中已上传的原件。PDF 按页插入，页序留空表示全部页；相同内容只编入一次并保留多处引用。</p>
    <div v-for="o in options" :key="o.key" class="py-1"><label><input type="checkbox" :disabled="!o.available" :checked="modelValue.some(r => key(r) === key(o))" @change="select(o, $event.target.checked)" /> {{ o.caption }}（{{ o.page_count || 0 }} 页）</label><p v-if="o.problems?.length" class="text-warn">{{ o.problems.join('；') }}</p></div>
    <div v-for="(r, i) in modelValue" :key="key(r)" class="border-t border-line pt-2 mt-2 space-y-1">
      <label class="block">图题 <input :value="r.caption" class="bg-surface border border-line" @input="update(i, 'caption', $event.target.value)" /></label>
      <label class="block">页序 <input :value="r.pages.join(',')" placeholder="如 1,2,3" aria-label="附件页序" class="bg-surface border border-line" @change="update(i, 'pages', $event.target.value.trim() ? $event.target.value.split(/[,，\s]+/).map(Number) : [])" /></label>
      <label>位置 <select :value="r.placement" class="bg-surface" @change="update(i, 'placement', $event.target.value)"><option value="appendix">统一附件册</option><option value="section" :disabled="!r.section_id">对应章节后</option></select></label>
      <button :disabled="i === 0" class="text-accent-fg ml-2" @click="move(i, -1)">上移</button><button :disabled="i === modelValue.length - 1" class="text-accent-fg ml-2" @click="move(i, 1)">下移</button>
    </div>
  </details>
</template>
