import { describe, expect, it, vi } from 'vitest'
import { fetchRepoInfo, relativeTime } from '@/utils/github'

function res(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body }
}

describe('GitHub 仓库信息', () => {
  it('整理仓库统计与最近提交（只取提交说明首行、短 sha）', async () => {
    const fetchImpl = vi.fn((url) =>
      Promise.resolve(
        url.includes('/commits')
          ? res([{ sha: 'abcdef1234567', html_url: 'u1', commit: { message: '修复导出\n\n详细说明', committer: { date: '2026-10-06T11:30:55Z' } } }])
          : res({ stargazers_count: 3, forks_count: 1, open_issues_count: 2, license: null, default_branch: 'master', pushed_at: '2026-10-06T11:30:55Z' })
      )
    )
    const info = await fetchRepoInfo({ fetchImpl, useCache: false })
    expect(info).toMatchObject({ stars: 3, forks: 1, openIssues: 2, license: '', defaultBranch: 'master' })
    expect(info.commits).toEqual([{ sha: 'abcdef1', message: '修复导出', date: '2026-10-06T11:30:55Z', url: 'u1' }])
    expect(fetchImpl).toHaveBeenCalledWith('https://api.github.com/repos/shyrel666/EasyWrite', expect.anything())
  })

  it('限流或离线时抛错，提交列表失败不影响仓库统计', async () => {
    const limited = vi.fn(() => Promise.resolve(res({ message: 'rate limit' }, 403)))
    await expect(fetchRepoInfo({ fetchImpl: limited, useCache: false })).rejects.toThrow('限流')
    const noCommits = vi.fn((url) => Promise.resolve(url.includes('/commits') ? res({}, 500) : res({ stargazers_count: 0 })))
    expect((await fetchRepoInfo({ fetchImpl: noCommits, useCache: false })).commits).toEqual([])
  })

  it('相对时间', () => {
    const now = new Date('2026-10-07T12:00:00Z')
    expect(relativeTime('2026-10-07T11:59:30Z', now)).toBe('刚刚')
    expect(relativeTime('2026-10-07T11:15:00Z', now)).toBe('45 分钟前')
    expect(relativeTime('2026-10-07T07:00:00Z', now)).toBe('5 小时前')
    expect(relativeTime('2026-10-04T12:00:00Z', now)).toBe('3 天前')
    expect(relativeTime('', now)).toBe('—')
  })
})
