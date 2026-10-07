import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'dashboard', component: () => import('@/views/DashboardView.vue'), meta: { title: '工作台' } },
  { path: '/project/:id/wizard', name: 'wizard', component: () => import('@/views/WizardView.vue'), meta: { title: '项目向导' } },
  { path: '/project/:id/workspace', name: 'workspace', component: () => import('@/views/WorkspaceView.vue'), meta: { title: '标书编纂' } },
  { path: '/project/:id/deviation', name: 'deviation', component: () => import('@/views/DeviationView.vue'), meta: { title: '技术偏离表' } },
  { path: '/project/:id/quality', name: 'quality', component: () => import('@/views/QualityView.vue'), meta: { title: '质检与合规' } },
  { path: '/knowledge', name: 'knowledge', component: () => import('@/views/KnowledgeView.vue'), meta: { title: '知识库' } },
  { path: '/assets', name: 'assets', component: () => import('@/views/AssetsView.vue'), meta: { title: '企业资产' } },
  { path: '/templates', name: 'templates', component: () => import('@/views/TemplatesView.vue'), meta: { title: '模板中心' } },
  { path: '/settings', name: 'settings', component: () => import('@/views/SettingsView.vue'), meta: { title: '系统设置' } },
  { path: '/about', name: 'about', component: () => import('@/views/AboutView.vue'), meta: { title: '关于' } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({ history: createWebHistory(), routes })

router.afterEach((to, from, failure) => {
  // 被守卫拦下的导航（如设置页有未保存修改时选择留下）不改标题
  if (failure) return
  document.title = to.meta.title ? `${to.meta.title} · EasyWrite` : 'EasyWrite'
})

export default router
