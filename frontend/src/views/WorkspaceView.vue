<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useProjectStore } from '@/stores/project'
import { useAiStore } from '@/stores/ai'
import TaskProgress from '@/components/common/TaskProgress.vue'
import AppShell from '@/components/layout/AppShell.vue'
import OutlineTree from '@/components/workspace/OutlineTree.vue'
import SectionEditor from '@/components/workspace/SectionEditor.vue'
import ReferencePanel from '@/components/workspace/ReferencePanel.vue'
import { sectionStatus } from '@/utils/project'

const route = useRoute()
const store = useProjectStore()
const ai = useAiStore()
const projectId = route.params.id

const selectedId = ref('')
// 宽屏三栏常驻；较窄时大纲（<1024）与参考栏（<1280）改为浮层；参考栏在 1440 以下默认收起
const viewportWidth = ref(window.innerWidth)
const leftOpen = ref(viewportWidth.value >= 1024)
const rightOpen = ref(viewportWidth.value >= 1440)
const narrow = computed(() => (leftOpen.value && viewportWidth.value < 1024) || (rightOpen.value && viewportWidth.value < 1280))
function onResize() {
  viewportWidth.value = window.innerWidth
}
window.addEventListener('resize', onResize)
onUnmounted(() => window.removeEventListener('resize', onResize))
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

function onRefsUpdated(refs) {
  if (selectedNode.value) selectedNode.value.last_refs = refs
}

onMounted(async () => {
  // 强制重新加载：偏离表回填等其他页面的写入不能被 store 缓存遮蔽
  const p = await store.load(projectId, true)
  if (!p.outline?.length) {
    ElMessage.warning('该项目尚未规划大纲，请先完成向导')
  } else if (p.outline.length) {
    // 默认选中第一个叶节点
    const firstLeaf = findFirstLeaf(p.outline)
    selectedId.value = firstLeaf?.id || p.outline[0].id
  }
  rightRef.value?.open()
  // 刷新页面或从其他页面回来时，接管仍在进行的批量撰写
  try {
    const { tasks } = await api.listTasks({ projectId, type: 'section_batch', active: true, limit: 1 })
    if (tasks.length) {
      batchTaskId.value = tasks[0].id
      batchOpen.value = true
    }
  } catch { /* 任务查询失败不影响编纂 */ }
})

function findFirstLeaf(nodes) {
  for (const n of nodes) {
    if (!n.children || !n.children.length) return n
    const leaf = findFirstLeaf(n.children)
    if (leaf) return leaf
  }
  return null
}

// ---------- 批量撰写 ----------
const batchOpen = ref(false)
const batchIncludeWritten = ref(false)
const batchTaskId = ref('')
const batchStarting = ref(false)
const editorKey = ref(0)

function collectLeaves(nodes, out = []) {
  for (const n of nodes || []) {
    if (n.children?.length) collectLeaves(n.children, out)
    else out.push(n)
  }
  return out
}
// 与后端规则一致：AI 撰写模式的叶节点，跳过已校审；默认只写空白章节
const batchCandidates = computed(() => {
  const leaves = collectLeaves(store.outline).filter((n) => (n.content_mode || 'ai_generate') === 'ai_generate' && n.status !== 'reviewed')
  const empty = leaves.filter((n) => !(n.content || '').trim())
  return { empty: empty.length, written: leaves.length - empty.length }
})
const batchCount = computed(() => batchCandidates.value.empty + (batchIncludeWritten.value ? batchCandidates.value.written : 0))

async function startBatch() {
  batchStarting.value = true
  try {
    const res = await api.generateBatch(projectId, batchIncludeWritten.value)
    batchTaskId.value = res.task_id
  } catch (e) {
    ElMessage.error('启动失败：' + e.message)
  } finally {
    batchStarting.value = false
  }
}

async function onBatchDone(task) {
  batchTaskId.value = ''
  await store.load(projectId, true)
  editorKey.value += 1 // 重新挂载编辑器，载入新正文
  const r = task.result || {}
  if (task.status === 'completed') {
    const failed = r.failed?.length ? `，失败 ${r.failed.length} 节（${r.failed.map((f) => f.title).slice(0, 3).join('、')}）` : ''
    const skipped = r.skipped?.length ? `，跳过 ${r.skipped.length} 节（期间已被编辑）` : ''
    ElMessage[r.failed?.length ? 'warning' : 'success'](`批量撰写完成：写入 ${r.generated?.length || 0} 节${skipped}${failed}`)
  } else {
    ElMessage.warning(`批量撰写已${task.status === 'cancelled' ? '取消' : '中止'}，已写入的章节已保存`)
  }
  batchOpen.value = false
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
  <AppShell :project-id="projectId" :project-name="store.project?.name" active="workspace" export-enabled fill>
    <div class="h-full flex relative">
      <!-- 窄屏：浮层面板的遮罩 -->
      <div
        v-if="(leftOpen || rightOpen) && narrow"
        class="absolute inset-0 z-10 bg-black/25"
        @click="leftOpen = false; rightOpen = false"
      />

      <!-- ===== 左栏：大纲 ===== -->
      <aside
        v-if="leftOpen"
        class="w-[288px] shrink-0 bg-surface border-r border-line flex flex-col absolute inset-y-0 left-0 z-20 lg:static lg:z-auto shadow-float lg:shadow-none"
      >
        <div class="shrink-0 px-4 pt-3.5 pb-3 border-b border-line">
          <div class="flex items-center justify-between gap-2">
            <span class="text-sm font-semibold text-ink">大纲</span>
            <el-tooltip :content="ai.llmConfigured ? '按大纲顺序批量撰写空白章节' : '批量撰写需要先配置大模型'" placement="bottom">
              <span>
                <el-button size="small" :disabled="!ai.llmConfigured || !store.outline.length" @click="batchOpen = true">
                  <el-icon class="mr-1"><MagicStick /></el-icon>批量撰写
                </el-button>
              </span>
            </el-tooltip>
          </div>
          <div class="mt-3 flex items-center gap-2.5">
            <div class="meter flex-1"><span class="bg-ok" :style="{ width: `${progress.rate}%` }" /></div>
            <span class="text-2xs text-ink-2 num font-medium">{{ progress.done }}/{{ progress.total }}</span>
          </div>
          <div class="mt-2 flex items-center gap-3 text-2xs text-ink-3">
            <span v-for="s in ['pending', 'completed', 'reviewed']" :key="s" class="inline-flex items-center gap-1">
              <span class="w-[7px] h-[7px] rounded-full" :class="sectionStatus(s).dot" />{{ sectionStatus(s).label }}
            </span>
          </div>
        </div>
        <div class="flex-1 overflow-auto py-2">
          <OutlineTree :nodes="store.outline" :selected-id="selectedId" @select="selectNode" />
          <p v-if="!store.outline.length && !store.loading" class="px-4 py-8 text-xs text-ink-3 text-center">尚未规划大纲，请先完成项目向导。</p>
        </div>
      </aside>

      <!-- ===== 中栏：编辑器 ===== -->
      <section class="flex-1 min-w-0 flex flex-col">
        <SectionEditor
          :key="`${selectedId}-${editorKey}`"
          :node="selectedNode"
          :project-id="projectId"
          @refs-updated="onRefsUpdated"
        >
          <template #toolbar-start>
            <button class="icon-btn shrink-0" :class="{ 'is-active': leftOpen }" title="大纲" @click="leftOpen = !leftOpen">
              <el-icon :size="17"><Memo /></el-icon>
            </button>
          </template>
          <template #toolbar-end>
            <button class="icon-btn shrink-0" :class="{ 'is-active': rightOpen }" title="撰写依据 / 全局事实" @click="rightOpen = !rightOpen">
              <el-icon :size="17"><Notebook /></el-icon>
            </button>
          </template>
        </SectionEditor>
      </section>

      <!-- ===== 右栏：参考 ===== -->
      <aside
        v-if="rightOpen"
        class="w-[340px] max-w-[92vw] shrink-0 border-l border-line flex flex-col absolute inset-y-0 right-0 z-20 xl:static xl:z-auto shadow-float xl:shadow-none"
      >
        <ReferencePanel ref="rightRef" :node="selectedNode" :project-id="projectId" @close="rightOpen = false" />
      </aside>
    </div>

    <el-dialog v-model="batchOpen" title="批量撰写章节" width="min(500px, 94vw)" :close-on-click-modal="!batchTaskId">
      <template v-if="!batchTaskId">
        <p class="text-sm text-ink-2 leading-relaxed mb-4">
          按大纲顺序逐节调用大模型撰写（结合评分要点、全局事实与知识库）。已校审、点对点应答与模板填充章节不参与。
        </p>
        <div class="rounded-lg border border-line bg-raised px-4 py-3 mb-3 flex items-baseline justify-between">
          <span class="text-sm text-ink-2">空白章节</span>
          <span class="text-xl font-semibold num">{{ batchCandidates.empty }}<span class="text-xs text-ink-3 font-normal ml-1">节</span></span>
        </div>
        <el-checkbox v-model="batchIncludeWritten" :disabled="!batchCandidates.written">
          同时重写已有内容（未校审）的 {{ batchCandidates.written }} 节
        </el-checkbox>
        <p class="hint mt-2">覆盖前自动保存历史版本；撰写期间可继续编辑，你手动修改过的章节不会被覆盖。</p>
      </template>
      <TaskProgress
        v-else
        :task-id="batchTaskId"
        title="正在批量撰写章节"
        cancellable
        @done="onBatchDone"
        @failed="onBatchDone"
      />
      <template #footer>
        <template v-if="!batchTaskId">
          <el-button @click="batchOpen = false">取消</el-button>
          <el-button type="primary" :loading="batchStarting" :disabled="!batchCount" @click="startBatch">
            开始撰写 {{ batchCount }} 节
          </el-button>
        </template>
      </template>
    </el-dialog>
  </AppShell>
</template>
