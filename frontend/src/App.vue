<script setup>
import { onMounted, onUnmounted } from 'vue'
import { useAiStore } from '@/stores/ai'

const aiStore = useAiStore()

let timer = null
onMounted(() => {
  aiStore.refresh()
  timer = setInterval(() => aiStore.refresh(), 30000)
})
onUnmounted(() => timer && clearInterval(timer))
</script>

<template>
  <router-view />
</template>
