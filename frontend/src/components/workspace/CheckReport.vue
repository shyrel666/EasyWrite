<script setup>
import { computed } from 'vue'
import { ISSUE_SOURCE, VERIFY_KIND, groupIssues, reportVerdict } from '@/utils/sectionCheck'

const props = defineProps({
  report: { type: Object, default: null },
  // 可定位：点击原文片段时发出 locate（编辑器据此选中正文）
  locatable: { type: Boolean, default: false },
  // 候选稿面板把待核实事项单独成栏，这里不重复显示
  showVerify: { type: Boolean, default: true },
})
const emit = defineEmits(['locate'])

const groups = computed(() => groupIssues(props.report))
const verdict = computed(() => reportVerdict(props.report))
const missingPoints = computed(() => (props.report?.points || []).filter((p) => !p.mentioned).length)
</script>

<template>
  <div v-if="report" class="space-y-4 text-xs">
    <!-- 摘要 -->
    <div class="flex flex-wrap items-center gap-2">
      <span class="chip" :class="verdict.chip">{{ verdict.label }}</span>
      <span class="chip chip-warn" :class="{ 'chip-mute': !report.quality_count }">质量问题 {{ report.quality_count }}</span>
      <span class="chip chip-mute">待核实 {{ report.pending_verification.length }}</span>
      <span class="num text-ink-3">
        {{ report.char_count }} 字<template v-if="report.word_budget"> / 预算 {{ report.word_budget }}</template>
      </span>
    </div>
    <p class="hint">{{ report.llm_note }}<span v-if="report.checked_at"> · {{ report.checked_at }}</span></p>

    <!-- 阻塞 / 质量问题 -->
    <section v-for="g in [
      { key: 'blocking', title: '阻塞问题', empty: '没有阻塞问题', tone: 'text-bad' },
      { key: 'quality', title: '质量问题', empty: '没有质量问题', tone: 'text-warn' },
    ]" :key="g.key">
      <p class="font-semibold text-ink mb-1.5" :class="groups[g.key].length ? g.tone : ''">
        {{ g.title }}<span class="num text-ink-3 font-normal ml-1">{{ groups[g.key].length }}</span>
      </p>
      <p v-if="!groups[g.key].length" class="text-ink-3">{{ g.empty }}</p>
      <ul class="space-y-1.5">
        <li v-for="(it, i) in groups[g.key]" :key="`${g.key}-${i}`" class="rounded-lg border border-line px-2.5 py-2">
          <div class="flex items-start gap-1.5">
            <span class="chip shrink-0" :class="ISSUE_SOURCE[it.source]?.chip || 'chip-mute'">{{ ISSUE_SOURCE[it.source]?.label || it.source }}</span>
            <span class="text-ink-2 leading-relaxed flex-1 min-w-0">{{ it.message }}</span>
          </div>
          <button
            v-if="it.excerpt"
            class="mt-1 block w-full text-left text-2xs text-ink-3 leading-relaxed truncate"
            :class="locatable ? 'hover:text-accent-fg cursor-pointer' : 'cursor-default'"
            :title="locatable ? '在正文中定位' : it.excerpt"
            @click="locatable && emit('locate', it)"
          >
            <span v-if="it.line" class="num mr-1">第 {{ it.line }} 行</span>{{ it.excerpt }}
          </button>
        </li>
      </ul>
    </section>

    <!-- 评分要点 -->
    <section v-if="report.points.length">
      <p class="font-semibold text-ink mb-1.5">
        本节评分要点<span class="num text-ink-3 font-normal ml-1">{{ report.points.length - missingPoints }}/{{ report.points.length }}</span>
      </p>
      <ul class="space-y-1">
        <li v-for="(p, i) in report.points" :key="`pt-${i}`" class="flex items-center gap-1.5">
          <el-icon :class="p.mentioned ? 'text-ok' : 'text-bad'"><component :is="p.mentioned ? 'CircleCheck' : 'CircleClose'" /></el-icon>
          <span class="text-ink-2 truncate" :title="p.item_name ? `评分项：${p.item_name}` : ''">{{ p.point }}</span>
        </li>
      </ul>
    </section>

    <!-- 待核实事项 -->
    <section v-if="showVerify">
      <p class="font-semibold text-ink mb-1.5">
        待核实事项<span class="num text-ink-3 font-normal ml-1">{{ report.pending_verification.length }}</span>
      </p>
      <p v-if="!report.pending_verification.length" class="text-ink-3">没有需要核实的承诺数值、占位或资料</p>
      <ul class="space-y-1.5">
        <li v-for="(v, i) in report.pending_verification" :key="`v-${i}`" class="leading-relaxed">
          <span class="chip chip-warn mr-1">{{ VERIFY_KIND[v.kind] || v.kind }}</span>
          <button
            class="text-ink-2 text-left"
            :class="locatable ? 'hover:text-accent-fg' : 'cursor-default'"
            @click="locatable && emit('locate', { excerpt: v.text, line: v.line })"
          >{{ v.text }}</button>
          <span v-if="v.note && v.kind !== 'placeholder'" class="block text-2xs text-ink-3">{{ v.note }}</span>
        </li>
      </ul>
    </section>
  </div>
</template>
