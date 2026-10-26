import { expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AppendixPlanner from '@/components/evidence/AppendixPlanner.vue'

it('选择、页序、排序及缺口提示', async () => {
  const options = [1, 2].map(i => ({ key: `q:a${i}`, asset_key: 'q', attachment_id: `a${i}`, caption: `附件${i}`, page_count: 3, available: true, section_id: 's', placement: 'appendix' }))
  options.push({ key: 'missing', caption: '缺失证书', available: false, problems: ['关联失效'] })
  const w = mount(AppendixPlanner, { props: { options, modelValue: [], 'onUpdate:modelValue': v => w.setProps({ modelValue: v }) } })
  expect(w.text()).toContain('关联失效')
  expect(w.findAll('input[type="checkbox"]')[2].element.disabled).toBe(true)
  await w.findAll('input[type="checkbox"]')[0].setValue(true)
  await w.findAll('input[type="checkbox"]')[1].setValue(true)
  await w.findAll('input[aria-label="附件页序"]')[0].setValue('3,1,2')
  expect(w.props('modelValue')[0].pages).toEqual([3, 1, 2])
  await w.findAll('button').find(b => b.text() === '下移').trigger('click')
  expect(w.props('modelValue')[0].attachment_id).toBe('a2')
  await w.findAll('select')[0].setValue('section')
  expect(w.props('modelValue')[0].placement).toBe('section')
  w.unmount()
})
