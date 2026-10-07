import { describe, expect, it } from 'vitest'
import { projectEntry, sectionStatus, stageIndex, stageLabel } from '@/utils/project'

describe('project helpers', () => {
  it('阶段序号与名称，未知阶段回退到第一步', () => {
    expect(stageIndex('writing')).toBe(3)
    expect(stageIndex('unknown')).toBe(0)
    expect(stageLabel('tender_analyzed')).toBe('待规划大纲')
  })

  it('未确认大纲的项目回到向导，其余进入编纂台', () => {
    expect(projectEntry({ id: 'p1', stage: 'created' })).toBe('/project/p1/wizard')
    expect(projectEntry({ id: 'p1', stage: 'tender_analyzed' })).toBe('/project/p1/wizard')
    expect(projectEntry({ id: 'p1', stage: 'outline_confirmed' })).toBe('/project/p1/workspace')
  })

  it('未知章节状态按待写显示', () => {
    expect(sectionStatus('reviewed').label).toBe('已校审')
    expect(sectionStatus(undefined).label).toBe('待写')
  })
})
