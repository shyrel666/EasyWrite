import { defineStore } from 'pinia'
import api from '@/api/client'
import { isActiveTask } from '@/utils/tasks'

// 刷新间隔：任务面板打开时 / 仅有进行中任务时
const FAST_MS = 1500
const SLOW_MS = 5000

let timer = null

/**
 * 最近后台任务（任务中心共用一份数据与一个定时器，桌面/移动端两个入口不重复轮询）。
 */
export const useTaskStore = defineStore('tasks', {
  state: () => ({
    tasks: [],
    error: '',
    watchers: 0,
    openPanels: 0,
  }),
  getters: {
    activeCount: (s) => s.tasks.filter(isActiveTask).length,
  },
  actions: {
    async refresh() {
      clearTimeout(timer)
      timer = null
      try {
        this.tasks = (await api.listTasks({ limit: 20 })).tasks
        this.error = ''
      } catch (e) {
        this.error = e.message
      }
      this.schedule()
    },
    schedule() {
      clearTimeout(timer)
      timer = null
      if (!this.watchers) return
      if (this.openPanels) timer = setTimeout(() => this.refresh(), FAST_MS)
      else if (this.activeCount) timer = setTimeout(() => this.refresh(), SLOW_MS)
    },
    start() {
      this.watchers += 1
      if (this.watchers === 1) this.refresh()
    },
    stop() {
      this.watchers = Math.max(0, this.watchers - 1)
      if (!this.watchers) {
        clearTimeout(timer)
        timer = null
      }
    },
    setPanelOpen(open) {
      this.openPanels = Math.max(0, this.openPanels + (open ? 1 : -1))
      if (open) this.refresh()
      else this.schedule()
    },
  },
})
