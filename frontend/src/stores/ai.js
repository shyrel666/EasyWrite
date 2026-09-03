import { defineStore } from 'pinia'
import api from '@/api/client'

export const useAiStore = defineStore('ai', {
  state: () => ({
    loaded: false,
    llmConfigured: false,
    llmModel: '',
    embeddingAvailable: false,
    embeddingModel: '',
    lastMode: 'mock',
  }),
  getters: {
    /** 离线演示模式：未配置 LLM 或最近一次调用走了模拟器 */
    isMockMode: (s) => !s.llmConfigured || s.lastMode === 'mock',
  },
  actions: {
    async refresh() {
      try {
        const st = await api.aiStatus()
        this.llmConfigured = st.llm_configured
        this.llmModel = st.llm_model
        this.embeddingAvailable = st.embedding_available
        this.embeddingModel = st.embedding_model
        this.lastMode = st.last_mode
        this.loaded = true
      } catch {
        this.llmConfigured = false
        this.loaded = true
      }
    },
    noteMode(mode) {
      if (mode) this.lastMode = mode
    },
  },
})
