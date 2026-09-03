<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useProjectStore } from '@/stores/project'
import AppShell from '@/components/layout/AppShell.vue'
import OutlineTree from '@/components/workspace/OutlineTree.vue'
import SectionEditor from '@/components/workspace/SectionEditor.vue'
import ReferencePanel from '@/components/workspace/ReferencePanel.vue'

const route = useRoute()
const store = useProjectStore()
const projectId = route.params.id

const selectedId = ref('')
const leftOpen = ref(true)
const rightOpen = ref(true)
const rightRef = ref(null)

const selectedNode = computed(() => store.findNode(selectedId.value))

async function selectNode(node) {
  if (!node.children || !node.children.length) {
    selectedId.value = node.id
    // 移动端：选中后收起大纲抽屉
    if (window.innerWidth < 1024) leftOpen.value = false
    return
  }
  // 有子节点的容器章节也可撰写（如整章综述）
  selectedId.value = node.id
  if (window.innerWidth < 1024) leftOpen.value = false
}

function onNodeUpdated({ id, status }) {
  store.setSectionStatus(id, status)
}

function onRefsUpdated(refs) {
  if (selectedNode.value) selectedNode.value.last_refs = refs
}

onMounted(async () => {
  const p = await store.load(projectId)
  if (!p.outline?.length) {
    ElMessage.warning('该项目尚未规划大纲，请先完成向导')
  } else if (p.outline.length) {
    // 默认选中第一个叶节点
    const firstLeaf = findFirstLeaf(p.outline)
    selectedId.value = firstLeaf?.id || p.outline[0].id
  }
  rightRef.value?.open()
})

function findFirstLeaf(nodes) {
  for (const n of nodes) {
    if (!n.children || !n.children.length) return n
    const leaf = findFirstLeaf(n.children)
    if (leaf) return leaf
  }
  return null
}

const progress = computed(() => {
  const walk = (nodes) => {
    let total = 0, done = 0
    for (const n of nodes) {
      total += 1
      if (['completed', 'reviewed'].includes(n.status)) done += 1
      if (n.children) {
        const [t, d] = walk(n.children)
        total += t; done += d
      }
    }
    return [total, done]
  }
  const [total, done] = walk(store.outline)
  return { total, done, rate: total ? Math.round(done / total * 100) : 0 }
})
</script>

<template>
  <AppShell :project-id="projectId" :project-name="store.project?.name" active="workspace" export-enabled>
    <div class="flex h-[calc(100vh-7rem)] md:h-[calc(100vh-6.5rem)] relative">
      <!-- ===== 左栏：大纲树 ===== -->
      <aside
        v-if="leftOpen"
        class="w-72 shrink-0 bg-white border-r border-slate-200 flex flex-col absolute inset-y-0 left-0 z-20 md:static md:z-auto shadow-lg md:shadow-none"
      >
        <div class="flex items-center justify-between px-3 py-2 border-b border-slate-200 shrink-0">
          <span class="text-sm font-bold text-slate-700">大纲树</span>
          <div class="flex items-center gap-2">
            <el-progress type="circle" :percentage="progress.rate" :width="30" :stroke-width="5" />
            <button class="md:hidden text-slate-400" @click="leftOpen = false"><el-icon><Close /></el-icon></button>
          </div>
        </div>
        <div class="flex-1 overflow-auto py-1">
          <OutlineTree :nodes="store.outline" :selected-id="selectedId" @select="selectNode" />
        </div>
      </aside>
      <button
        v-if="!leftOpen"
        class="md:hidden absolute left-2 top-3 z-10 bg-white border border-slate-200 rounded-md p-1.5 shadow"
        @click="leftOpen = true"
      >
        <el-icon><Menu /></el-icon>
      </button>

      <!-- ===== 中栏：编辑器 ===== -->
      <section class="flex-1 min-w-0 flex flex-col border-r border-slate-200 bg-white">
        <SectionEditor
          :key="selectedId"
          :node="selectedNode"
          :project-id="projectId"
          @node-updated="onNodeUpdated"
          @refs-updated="onRefsUpdated"
        />
      </section>

      <!-- ===== 右栏：参考抽屉 ===== -->
      <aside
        v-if="rightOpen"
        class="w-80 shrink-0 flex flex-col absolute inset-y-0 right-0 z-20 lg:static lg:z-auto shadow-lg lg:shadow-none"
      >
        <ReferencePanel ref="rightRef" :node="selectedNode" :project-id="projectId" />
      </aside>
      <button
        v-if="!rightOpen"
        class="absolute right-2 top-3 z-10 bg-white border border-slate-200 rounded-md p-1.5 shadow"
        @click="rightOpen = true"
      >
        <el-icon><Notebook /></el-icon>
      </button>
    </div>
  </AppShell>
</template>
