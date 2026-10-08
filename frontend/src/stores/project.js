import { defineStore } from 'pinia'
import api from '@/api/client'

/** 当前项目上下文仓库：加载项目、局部更新辅助 */
export const useProjectStore = defineStore('project', {
  state: () => ({
    id: '',
    project: null,
    loading: false,
  }),
  getters: {
    outline: (s) => s.project?.outline || [],
    facts: (s) => s.project?.facts || null,
    stage: (s) => s.project?.stage || 'created',
  },
  actions: {
    async load(projectId, force = false) {
      if (!force && this.id === projectId && this.project) return this.project
      this.loading = true
      try {
        this.project = await api.getProject(projectId)
        this.id = projectId
        return this.project
      } finally {
        this.loading = false
      }
    },
    async saveFacts(facts) {
      const res = await api.updateFacts(this.id, facts)
      this.project.facts = res.facts
      return res.facts
    },
    async saveOutline(outline) {
      // 服务端合并：已有章节的正文、状态与修订号以服务端为准，返回合并后的大纲
      const res = await api.updateOutline(this.id, outline)
      this.project.outline = res.outline || outline
      if (res.stage) this.project.stage = res.stage
      return res
    },
    async saveSection(sectionId, content, status = 'completed') {
      const res = await api.saveSection(this.id, sectionId, content, status)
      this.setSectionStatus(sectionId, status)
      return res
    },
    setSectionStatus(sectionId, status) {
      const walk = (nodes) => {
        for (const n of nodes) {
          if (n.id === sectionId) {
            n.status = status
            return true
          }
          if (n.children && walk(n.children)) return true
        }
        return false
      }
      walk(this.outline)
    },
    setSectionContent(sectionId, content, status = 'completed') {
      const walk = (nodes) => {
        for (const n of nodes) {
          if (n.id === sectionId) {
            n.content = content
            n.status = status
            return true
          }
          if (n.children && walk(n.children)) return true
        }
        return false
      }
      walk(this.outline)
    },
    findNode(sectionId) {
      const walk = (nodes) => {
        for (const n of nodes) {
          if (n.id === sectionId) return n
          if (n.children) {
            const f = walk(n.children)
            if (f) return f
          }
        }
        return null
      }
      return walk(this.outline)
    },
  },
})
