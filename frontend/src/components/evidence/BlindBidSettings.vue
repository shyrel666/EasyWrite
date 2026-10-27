<script setup>
import { ref, watch } from 'vue'

const props = defineProps({ modelValue: { type: Object, required: true }, report: { type: Object, default: null } })
const emit = defineEmits(['update:modelValue', 'save', 'locate'])
const FONTS = ['宋体', '黑体', '仿宋', '楷体', 'Arial', 'Times New Roman']
function update(patch) { emit('update:modelValue', { ...props.modelValue, ...patch }) }

// 标识文本框保留输入原文（含行尾换行），只把解析出的非空行写回模型；外部刷新为不同列表时才覆盖原文
const parseIdentifiers = (text) => text.split('\n').map((s) => s.trim()).filter(Boolean)
const identifiersText = ref((props.modelValue.identifiers || []).join('\n'))
watch(() => props.modelValue.identifiers, (list) => {
  const text = (list || []).join('\n')
  if (parseIdentifiers(identifiersText.value).join('\n') !== text) identifiersText.value = text
})
function onIdentifiers(text) {
  identifiersText.value = text
  update({ identifiers: parseIdentifiers(text) })
}

// 输入过程中的空值或越界值不写入模型；提交时恢复为当前有效值，界面显示与保存值保持一致
const validSize = (raw) => raw !== '' && Number(raw) >= 8 && Number(raw) <= 24
function onFontSize(raw) { if (validSize(raw)) update({ font_size: Number(raw) }) }
function commitFontSize(el) { if (!validSize(el.value)) el.value = props.modelValue.font_size ?? 12 }
</script>
<template>
  <section class="border border-line rounded-lg p-3 my-3 space-y-2">
    <label><input type="checkbox" :checked="modelValue.enabled" @change="update({ enabled: $event.target.checked })" /> 受限暗标技术册</label>
    <template v-if="modelValue.enabled">
      <p class="hint">身份封面和证明附件已禁用；明标资格证明可单独成册。已知标识会在导出副本中替换，仍须逐页核对。</p>
      <label>统一字体 <select :value="modelValue.font_name || '宋体'" @change="update({ font_name: $event.target.value })"><option v-for="font in FONTS" :key="font">{{ font }}</option></select></label>
      <label>字号 <input type="number" min="8" max="24" step="0.5" :value="modelValue.font_size ?? 12" @input="onFontSize($event.target.value)" @change="commitFontSize($event.target)" /></label>
      <p class="hint">编号统一为十进制；页边距沿用所选模板。</p>
      <textarea class="input-base w-full" aria-label="暗标规则" :value="modelValue.rules" placeholder="招标方暗标格式要求原文" @input="update({ rules: $event.target.value })" />
      <textarea class="input-base w-full" aria-label="补充身份标识" :value="identifiersText" placeholder="补充别名、地址、账号等，每行一项" @input="onIdentifiers($event.target.value)" />
      <label><input type="checkbox" :checked="modelValue.omit_images" @change="update({ omit_images: $event.target.checked })" /> 我确认从技术册排除全部图片及图代码</label>
      <p v-if="report?.images?.length" class="text-warn">{{ report.images.length }} 个章节含图，未选择排除时将阻止暗标导出。</p>
      <button v-for="(hit, i) in report?.hits || []" :key="i" class="block text-accent-fg text-left" @click="emit('locate', hit.section_id)">{{ hit.section_title }}：{{ hit.identifier }}</button>
      <ul class="list-disc pl-5"><li v-for="item in report?.checklist || []" :key="item">{{ item }}</li></ul>
    </template>
    <button class="btn-secondary" @click="emit('save')">保存模式并刷新检查</button>
  </section>
</template>
