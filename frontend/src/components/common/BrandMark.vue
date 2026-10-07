<script setup>
import { computed, useId } from 'vue'

/**
 * 品牌标志：藏蓝底上的 E，右下一枚朱砂方印。
 * E 的三横是标书的章、节、条；朱砂方块既是句号也是印章——标书的最后一步是盖章，每条承诺都要落款负责。
 * 标志颜色固定，不随明暗主题变化；尺寸 ≥ 56 时方印加画印框。
 */
const props = defineProps({
  size: { type: Number, default: 32 },
})

// 渐变 id 每个实例唯一：同 id 的渐变若落在 display:none 的 SVG 里，其他实例会引用失败
const gid = `ew-mark-${useId()}`
const framed = computed(() => props.size >= 56)
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 48 48"
    role="img"
    aria-label="EasyWrite"
    class="brand-mark shrink-0"
  >
    <defs>
      <linearGradient :id="gid" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#2B4874" />
        <stop offset="1" stop-color="#18294A" />
      </linearGradient>
    </defs>
    <rect width="48" height="48" rx="12" :fill="`url(#${gid})`" />
    <rect x=".5" y=".5" width="47" height="47" rx="11.5" fill="none" stroke="#fff" stroke-opacity=".14" />
    <g fill="#F8F4EA">
      <rect x="13" y="12" width="4.8" height="24" rx="1.2" />
      <rect x="13" y="12" width="18.5" height="4.8" rx="1.2" />
      <rect x="13" y="21.6" width="13.5" height="4.8" rx="1.2" />
      <rect x="13" y="31.2" width="13.5" height="4.8" rx="1.2" />
    </g>
    <g class="brand-seal" transform="rotate(-6 33 32.6)">
      <rect x="29.4" y="29" width="7.2" height="7.2" rx="1.5" fill="#E0573C" />
      <rect
        v-if="framed"
        x="30.5"
        y="30.1"
        width="5"
        height="5"
        rx=".7"
        fill="none"
        stroke="#FFF1EC"
        stroke-width=".55"
        stroke-opacity=".85"
      />
    </g>
  </svg>
</template>

<style scoped>
.brand-seal {
  transform-box: fill-box;
  transform-origin: center;
  transition: transform 0.25s cubic-bezier(0.3, 1.5, 0.5, 1);
}
</style>
