<template>
  <div class="h-full flex flex-col bg-slate-100 text-slate-800">
    <!-- 顶部导航栏 -->
    <header class="bg-slate-900 text-white px-5 py-2.5 flex items-center justify-between shadow-md z-30 shrink-0">
      <div class="flex items-center space-x-6">
        <div class="flex items-center space-x-2.5 cursor-pointer">
          <div class="w-8 h-8 rounded bg-blue-600 flex items-center justify-center font-bold text-lg text-white shadow">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
            </svg>
          </div>
          <div class="font-bold text-base tracking-wide flex items-center gap-2">
            EasyWrite
            <span class="text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-300 font-normal border border-slate-700">技术标 Copilot</span>
          </div>
        </div>

        <!-- 导航选项卡 -->
        <nav class="flex items-center space-x-1 text-sm font-medium">
          <button 
            @click="activeTab = 'workspace'" 
            :class="activeTab === 'workspace' ? 'bg-blue-600 text-white' : 'text-slate-300 hover:text-white hover:bg-slate-800'"
            class="px-2.5 py-1.5 rounded-md transition">标书编纂台</button>
          <button 
            @click="activeTab = 'tender'" 
            :class="activeTab === 'tender' ? 'bg-blue-600 text-white' : 'text-slate-300 hover:text-white hover:bg-slate-800'"
            class="px-2.5 py-1.5 rounded-md transition">18项拆标台</button>
          <button 
            @click="activeTab = 'deviation'" 
            :class="activeTab === 'deviation' ? 'bg-blue-600 text-white' : 'text-slate-300 hover:text-white hover:bg-slate-800'"
            class="px-2.5 py-1.5 rounded-md transition">技术偏离表</button>
          <button 
            @click="activeTab = 'compliance'" 
            :class="activeTab === 'compliance' ? 'bg-blue-600 text-white' : 'text-slate-300 hover:text-white hover:bg-slate-800'"
            class="px-2.5 py-1.5 rounded-md transition">八维质检与合规</button>
          <button 
            @click="activeTab = 'assets'" 
            :class="activeTab === 'assets' ? 'bg-blue-600 text-white' : 'text-slate-300 hover:text-white hover:bg-slate-800'"
            class="px-2.5 py-1.5 rounded-md transition">企业资产中台</button>
          <button 
            @click="activeTab = 'knowledge'" 
            :class="activeTab === 'knowledge' ? 'bg-blue-600 text-white' : 'text-slate-300 hover:text-white hover:bg-slate-800'"
            class="px-2.5 py-1.5 rounded-md transition">历史标书库</button>
          <button 
            @click="activeTab = 'templates'" 
            :class="activeTab === 'templates' ? 'bg-blue-600 text-white' : 'text-slate-300 hover:text-white hover:bg-slate-800'"
            class="px-2.5 py-1.5 rounded-md transition">Word模板中心</button>
        </nav>
      </div>

      <div class="flex items-center space-x-3 text-xs">
        <button @click="exportWord" class="bg-emerald-600 hover:bg-emerald-700 text-white font-semibold px-3 py-1.5 rounded shadow">
          导出标书 Word
        </button>
      </div>
    </header>

    <!-- 工作台视口 -->
    <main class="flex-1 overflow-hidden">
      <iframe src="/static/index.html" class="w-full h-full border-none"></iframe>
    </main>
  </div>
</template>

<script setup>
import { ref } from 'vue'
const activeTab = ref('workspace')
const exportWord = () => {
  window.open('/api/v1/project/export', '_blank')
}
</script>
