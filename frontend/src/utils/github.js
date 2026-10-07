/**
 * 项目 GitHub 仓库信息：从 GitHub 公开接口读取 Star / Fork / Issue 与最近提交。
 * 本次会话内缓存 10 分钟（公开接口每小时 60 次限额）；离线或被限流时抛错，页面退回只显示静态链接。
 */
export const GITHUB_REPO = 'shyrel666/EasyWrite'
export const GITHUB_URL = `https://github.com/${GITHUB_REPO}`
export const GITHUB_CLONE = `${GITHUB_URL}.git`

const API = `https://api.github.com/repos/${GITHUB_REPO}`
const CACHE_KEY = 'easywrite.github'
const TTL_MS = 10 * 60 * 1000

function readCache(now) {
  try {
    const cached = JSON.parse(sessionStorage.getItem(CACHE_KEY) || 'null')
    if (cached && now - cached.at < TTL_MS) return cached.info
  } catch { /* 存储不可用时不缓存 */ }
  return null
}

function writeCache(info, now) {
  try {
    sessionStorage.setItem(CACHE_KEY, JSON.stringify({ at: now, info }))
  } catch { /* 忽略 */ }
}

export async function fetchRepoInfo({ fetchImpl = globalThis.fetch, now = Date.now(), useCache = true } = {}) {
  if (useCache) {
    const cached = readCache(now)
    if (cached) return cached
  }
  const headers = { Accept: 'application/vnd.github+json' }
  const [repoRes, commitsRes] = await Promise.all([
    fetchImpl(API, { headers }),
    fetchImpl(`${API}/commits?per_page=5`, { headers }),
  ])
  if (!repoRes.ok) throw new Error(repoRes.status === 403 ? 'GitHub 接口限流，请稍后再试' : `HTTP ${repoRes.status}`)
  const repo = await repoRes.json()
  const commits = commitsRes.ok ? await commitsRes.json() : []
  const info = {
    description: repo.description || '',
    stars: repo.stargazers_count ?? 0,
    forks: repo.forks_count ?? 0,
    openIssues: repo.open_issues_count ?? 0,
    watchers: repo.subscribers_count ?? null,
    license: repo.license?.spdx_id && repo.license.spdx_id !== 'NOASSERTION' ? repo.license.spdx_id : '',
    defaultBranch: repo.default_branch || 'master',
    createdAt: repo.created_at || '',
    pushedAt: repo.pushed_at || '',
    commits: (Array.isArray(commits) ? commits : []).map((c) => ({
      sha: (c.sha || '').slice(0, 7),
      message: (c.commit?.message || '').split('\n')[0],
      date: c.commit?.committer?.date || c.commit?.author?.date || '',
      url: c.html_url || '',
    })),
  }
  if (useCache) writeCache(info, now)
  return info
}

/** ISO 时间 → "刚刚" / "5 分钟前" / "3 小时前" / "2 天前"，超过 30 天显示日期 */
export function relativeTime(iso, now = new Date()) {
  if (!iso) return '—'
  const t = new Date(iso)
  if (Number.isNaN(t.getTime())) return '—'
  const sec = Math.max(0, (now.getTime() - t.getTime()) / 1000)
  if (sec < 60) return '刚刚'
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前`
  if (sec < 86400) return `${Math.floor(sec / 3600)} 小时前`
  if (sec < 86400 * 30) return `${Math.floor(sec / 86400)} 天前`
  const mm = String(t.getMonth() + 1).padStart(2, '0')
  const dd = String(t.getDate()).padStart(2, '0')
  return `${t.getFullYear()}-${mm}-${dd}`
}
