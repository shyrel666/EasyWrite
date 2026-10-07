<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import AppShell from '@/components/layout/AppShell.vue'
import BrandMark from '@/components/common/BrandMark.vue'
import GithubMark from '@/components/common/GithubMark.vue'
import { GITHUB_CLONE, GITHUB_REPO, GITHUB_URL, fetchRepoInfo, relativeTime } from '@/utils/github'

const router = useRouter()

const version = ref('')

// ---- GitHub：实时数据来自公开接口，取不到时只显示链接 ----
const repo = ref(null)
const repoState = ref('loading') // loading | ok | error
const repoError = ref('')

const REPO_LINKS = [
  { icon: 'ChatLineSquare', label: '提交问题反馈', text: '报告缺陷或提出改进建议', href: `${GITHUB_URL}/issues/new` },
  { icon: 'Tickets', label: '全部 Issue', text: '查看已知问题与讨论', href: `${GITHUB_URL}/issues` },
  { icon: 'Clock', label: '提交历史', text: '每次改动的说明', href: `${GITHUB_URL}/commits` },
  { icon: 'Box', label: '发行版本', text: '打包好的版本与更新说明', href: `${GITHUB_URL}/releases` },
]

async function loadRepo(force = false) {
  repoState.value = 'loading'
  try {
    repo.value = await fetchRepoInfo({ useCache: !force })
    repoState.value = 'ok'
  } catch (e) {
    repoError.value = e.message === 'Failed to fetch' ? '无法连接 GitHub（离线或网络受限）' : e.message
    repoState.value = 'error'
  }
}

async function copyClone() {
  try {
    await navigator.clipboard.writeText(`git clone ${GITHUB_CLONE}`)
    ElMessage.success('已复制克隆命令')
  } catch {
    ElMessage.warning('浏览器不允许写入剪贴板，请手动复制')
  }
}

onMounted(async () => {
  loadRepo()
  try {
    const res = await api.health()
    version.value = res.version || ''
  } catch {
    /* 后端不可达时不显示版本号 */
  }
})
</script>

<template>
  <AppShell active="about">
    <div class="max-w-5xl mx-auto px-4 sm:px-8 py-6 sm:py-10 space-y-6 sm:space-y-8">
      <!-- ======= 题头 ======= -->
      <section class="about-hero card overflow-hidden rise">
        <div class="px-6 sm:px-10 py-9 sm:py-14 flex flex-col sm:flex-row sm:items-center gap-7 sm:gap-10">
          <div class="mark-pad">
            <BrandMark :size="112" />
          </div>
          <div class="min-w-0">
            <p class="eyebrow">关于</p>
            <div class="mt-2.5 flex items-center gap-3 flex-wrap">
              <h1 class="text-[34px] sm:text-[42px] font-semibold tracking-tight leading-none text-ink">EasyWrite</h1>
              <span v-if="version" class="chip chip-accent num">v{{ version }}</span>
            </div>
            <p class="mt-2.5 text-sm text-ink-2">智能招投标技术标编纂系统</p>
            <p class="mt-6 font-kai text-[19px] sm:text-[21px] leading-relaxed text-ink max-w-xl">
              把一份招标文件，写成一份评分项逐条有着落、红线逐条有回应的技术标。
            </p>
            <div class="mt-7 flex items-center gap-2 flex-wrap">
              <el-button type="primary" @click="router.push('/')">
                <el-icon class="mr-1.5"><House /></el-icon>回到工作台
              </el-button>
              <el-button tag="a" :href="GITHUB_URL" target="_blank" rel="noopener noreferrer">
                <GithubMark class="mr-1.5" />GitHub
              </el-button>
              <el-button tag="a" href="/docs" target="_blank" rel="noopener">
                <el-icon class="mr-1.5"><Reading /></el-icon>接口文档
              </el-button>
            </div>
          </div>
        </div>
      </section>

      <!-- ======= GitHub ======= -->
      <section class="card overflow-hidden rise" style="animation-delay: 40ms">
        <div class="px-6 sm:px-8 pt-6 pb-5 flex items-start justify-between gap-4 flex-wrap">
          <div class="flex items-center gap-3.5 min-w-0">
            <span class="w-11 h-11 rounded-xl bg-ink text-paper flex items-center justify-center shrink-0">
              <GithubMark :size="22" />
            </span>
            <div class="min-w-0">
              <p class="eyebrow">开源仓库</p>
              <a :href="GITHUB_URL" target="_blank" rel="noopener noreferrer" class="mt-1 block text-lg font-semibold text-ink hover:text-accent-fg transition-colors truncate">
                {{ GITHUB_REPO.split('/')[0] }} <span class="text-ink-3 font-normal">/</span> {{ GITHUB_REPO.split('/')[1] }}
              </a>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <button class="icon-btn" title="刷新 GitHub 数据" :disabled="repoState === 'loading'" @click="loadRepo(true)">
              <el-icon :class="{ 'animate-spin': repoState === 'loading' }"><Refresh /></el-icon>
            </button>
            <el-button tag="a" :href="GITHUB_URL" target="_blank" rel="noopener noreferrer">
              在 GitHub 查看<el-icon class="ml-1"><TopRight /></el-icon>
            </el-button>
          </div>
        </div>

        <!-- 统计 -->
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-px bg-line border-y border-line">
          <div v-for="stat in [
            { icon: 'Star', label: 'Star', value: repo?.stars },
            { icon: 'Share', label: 'Fork', value: repo?.forks },
            { icon: 'ChatDotSquare', label: '开放的 Issue', value: repo?.openIssues },
            { icon: 'Clock', label: '最近推送', value: repo ? relativeTime(repo.pushedAt) : undefined },
          ]" :key="stat.label" class="bg-surface px-6 sm:px-8 py-4">
            <p class="text-2xs text-ink-3 flex items-center gap-1.5"><el-icon><component :is="stat.icon" /></el-icon>{{ stat.label }}</p>
            <p v-if="repoState === 'ok'" class="mt-1.5 text-xl font-semibold num text-ink leading-none">{{ stat.value }}</p>
            <p v-else-if="repoState === 'loading'" class="mt-1.5 h-5 w-12 rounded skeleton" />
            <p v-else class="mt-1.5 text-xl font-semibold text-ink-3 leading-none">—</p>
          </div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
          <!-- 最近提交 -->
          <div class="px-6 sm:px-8 py-5 border-b md:border-b-0 md:border-r border-line">
            <div class="flex items-baseline justify-between gap-3">
              <h3 class="text-[13px] font-semibold text-ink">最近提交</h3>
              <span v-if="repoState === 'ok'" class="text-2xs text-ink-3">
                {{ repo.defaultBranch }} 分支<template v-if="repo.license"> · {{ repo.license }}</template>
              </span>
            </div>
            <div v-if="repoState === 'loading'" class="mt-4 space-y-3">
              <div v-for="i in 3" :key="i" class="h-4 rounded skeleton" />
            </div>
            <div v-else-if="repoState === 'error'" class="mt-4 note note-warn">
              <el-icon class="mt-0.5 text-warn"><WarningFilled /></el-icon>
              <span>{{ repoError }}。仓库链接仍可直接打开。</span>
            </div>
            <p v-else-if="!repo.commits.length" class="mt-4 text-xs text-ink-3">暂无提交记录</p>
            <ol v-else class="mt-3">
              <li v-for="c in repo.commits" :key="c.sha">
                <a
                  :href="c.url"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="group flex items-center gap-3 py-2 -mx-2 px-2 rounded-lg hover:bg-raised transition-colors"
                >
                  <span class="font-mono text-2xs text-accent-fg bg-accent-soft rounded px-1.5 py-0.5 shrink-0">{{ c.sha }}</span>
                  <span class="flex-1 min-w-0 text-xs text-ink truncate" :title="c.message">{{ c.message }}</span>
                  <span class="text-2xs text-ink-3 shrink-0">{{ relativeTime(c.date) }}</span>
                </a>
              </li>
            </ol>
          </div>

          <!-- 链接与克隆 -->
          <div class="px-6 sm:px-8 py-5">
            <h3 class="text-[13px] font-semibold text-ink">参与与获取</h3>
            <ul class="mt-3 -mx-2">
              <li v-for="l in REPO_LINKS" :key="l.label">
                <a :href="l.href" target="_blank" rel="noopener noreferrer" class="group flex items-center gap-3 py-2 px-2 rounded-lg hover:bg-raised transition-colors">
                  <el-icon :size="16" class="text-ink-3 group-hover:text-accent-fg transition-colors"><component :is="l.icon" /></el-icon>
                  <span class="flex-1 min-w-0">
                    <span class="block text-xs font-medium text-ink">{{ l.label }}</span>
                    <span class="block text-2xs text-ink-3 truncate">{{ l.text }}</span>
                  </span>
                  <el-icon :size="12" class="text-ink-3"><TopRight /></el-icon>
                </a>
              </li>
            </ul>
            <div class="mt-3 flex items-center gap-2 rounded-lg border border-line bg-raised pl-3 pr-1 h-9">
              <code class="flex-1 min-w-0 truncate font-mono text-2xs text-ink-2">git clone {{ GITHUB_CLONE }}</code>
              <button class="icon-btn !w-7 !h-7" title="复制克隆命令" @click="copyClone"><el-icon><CopyDocument /></el-icon></button>
            </div>
          </div>
        </div>
        <p class="px-6 sm:px-8 py-2.5 border-t border-line bg-raised text-2xs text-ink-3">
          实时数据来自 GitHub 公开接口，只读取仓库的公开信息；离线时只显示链接。
        </p>
      </section>
    </div>
  </AppShell>
</template>

<style scoped>
.about-hero {
  background:
    radial-gradient(640px circle at 100% 0%, rgb(var(--c-accent) / 0.1), transparent 65%),
    radial-gradient(circle at 1px 1px, rgb(var(--c-ink) / 0.07) 1px, transparent 0) 0 0 / 18px 18px,
    rgb(var(--c-surface));
}
.mark-pad {
  @apply shrink-0 self-start sm:self-auto rounded-[28px];
  box-shadow: 0 18px 40px -16px rgb(24 41 74 / 0.55), 0 2px 6px rgb(24 41 74 / 0.18);
}
.mark-pad:hover :deep(.brand-seal) {
  transform: rotate(-16deg) scale(1.12);
}
</style>
