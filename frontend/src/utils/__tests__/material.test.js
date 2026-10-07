import { describe, expect, it } from 'vitest'
import { assetName, formatSize, materialChip } from '@/utils/material'

describe('material helpers', () => {
  it('证明材料状态映射到胶囊样式，未知状态按灰色显示', () => {
    expect(materialChip('齐备')).toBe('chip-ok')
    expect(materialChip('缺附件')).toBe('chip-warn')
    expect(materialChip('过期')).toBe('chip-bad')
    expect(materialChip('未关联')).toBe('chip-bad')
    expect(materialChip('其他')).toBe('chip-mute')
  })

  it('附件大小', () => {
    expect(formatSize(512)).toBe('512 B')
    expect(formatSize(2048)).toBe('2 KB')
    expect(formatSize(3 * 1024 * 1024)).toBe('3.0 MB')
  })

  it('业绩用项目名称作为显示名', () => {
    expect(assetName('cases', { id: 'c1', project_name: '某业绩' })).toBe('某业绩')
    expect(assetName('qualifications', { id: 'q1', name: 'ISO9001' })).toBe('ISO9001')
    expect(assetName('personnel', { id: 'p1' })).toBe('p1')
  })
})
