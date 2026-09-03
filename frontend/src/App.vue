<script setup>
import { onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAiStore } from '@/stores/ai'
import ModeBanner from '@/components/common/ModeBanner.vue'

const route = useRoute()
const aiStore = useAiStore()

let timer = null
onMounted(() => {
  aiStore.refresh()
  timer = setInterval(() => aiStore.refresh(), 30000)
})
onUnmounted(() => timer && clearInterval(timer))
</script>

<template>
  <div class="min-h-screen bg-slate-100 text-slate-800 flex flex-col">
    <ModeBanner v-if="route.name !== 'settings'" />
    <router-view class="flex-1" />
  </div>
</template>
