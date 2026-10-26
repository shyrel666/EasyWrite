import { expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import CommitmentEditor from '@/components/evidence/CommitmentEditor.vue'

it('采购要求先加入待确认，分级编辑后须重新确认，支持拒绝和保存恢复', async () => {
  const r = { id: 'r1', topic: 'response', value: '2', unit: '小时', comparator: 'le', severity: '重大故障', scope: '', subject: '', source: '招标原文', confirmed: false }
  const w = mount(CommitmentEditor, { props: { modelValue: [], requirements: [r], 'onUpdate:modelValue': v => w.setProps({ modelValue: v }) } })
  expect(w.text()).toContain('采购要求')
  await w.findAll('button').find(b => b.text() === '加入待确认').trigger('click')
  expect(w.props('modelValue')[0].confirmed).toBe(false)
  await w.get('input[type="checkbox"]').setValue(true)
  expect(w.props('modelValue')[0].confirmed).toBe(true)
  await w.get('input[aria-label="级别（如重大故障）"]').setValue('一般故障')
  expect(w.props('modelValue')[0].confirmed).toBe(false)
  await w.get('input[aria-label="数值"]').setValue('24')
  await w.get('input[type="checkbox"]').setValue(true)
  const saved = JSON.parse(JSON.stringify(w.props('modelValue')))
  w.unmount()
  const restored = mount(CommitmentEditor, { props: { modelValue: saved } })
  expect(restored.get('input[aria-label="数值"]').element.value).toBe('24')
  expect(restored.get('input[type="checkbox"]').element.checked).toBe(true)
  await restored.findAll('button').find(b => b.text().includes('拒绝')).trigger('click')
  expect(restored.emitted('update:modelValue')[0][0]).toEqual([])
  restored.unmount()
})
