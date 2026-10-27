import { expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import BlindBidSettings from '@/components/evidence/BlindBidSettings.vue'

it('模式切换、格式规则、身份命中及排除图片需要明确选择', async () => {
  const w = mount(BlindBidSettings, { props: { modelValue: { enabled: false }, report: { hits: [{ section_id: 's', section_title: '服务', identifier: '甲公司' }], images: [{}], checklist: ['逐页检查'] }, 'onUpdate:modelValue': v => w.setProps({ modelValue: v }) } })
  await w.get('input[type="checkbox"]').setValue(true)
  expect(w.text()).toContain('身份封面和证明附件已禁用')
  expect(w.text()).toContain('服务：甲公司')
  expect(w.text()).toContain('未选择排除时将阻止暗标导出')
  await w.findAll('input[type="checkbox"]')[1].setValue(true)
  expect(w.props('modelValue').omit_images).toBe(true)
  await w.get('[aria-label="暗标规则"]').setValue('不得含身份标识')
  expect(w.props('modelValue').rules).toBe('不得含身份标识')
})

function mountEnabled(modelValue) {
  const w = mount(BlindBidSettings, { props: { modelValue: { enabled: true, ...modelValue }, 'onUpdate:modelValue': v => w.setProps({ modelValue: v }) } })
  return w
}

it('补充标识可逐行录入：行尾换行保留，模型只收到去空白的非空行，外部刷新时同步', async () => {
  const w = mountEnabled({ identifiers: [] })
  const box = w.get('[aria-label="补充身份标识"]')
  await box.setValue('甲公司\n')
  expect(box.element.value).toBe('甲公司\n')
  expect(w.props('modelValue').identifiers).toEqual(['甲公司'])
  await box.setValue('甲公司\n 乙地址 \n')
  expect(box.element.value).toBe('甲公司\n 乙地址 \n')
  expect(w.props('modelValue').identifiers).toEqual(['甲公司', '乙地址'])
  await w.setProps({ modelValue: { enabled: true, identifiers: ['丙账号'] } })
  expect(box.element.value).toBe('丙账号')
})

it('字号清空或越界时不写入模型，提交后恢复为当前有效值', async () => {
  const w = mountEnabled({ font_size: 14 })
  const size = w.get('input[type="number"]')
  await size.setValue('')
  expect(w.props('modelValue').font_size).toBe(14)
  await size.trigger('change')
  expect(size.element.value).toBe('14')
  await size.setValue('30')
  await size.trigger('change')
  expect(w.props('modelValue').font_size).toBe(14)
  expect(size.element.value).toBe('14')
  await size.setValue('10.5')
  expect(w.props('modelValue').font_size).toBe(10.5)
})
