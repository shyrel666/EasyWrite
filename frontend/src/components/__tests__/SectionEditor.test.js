import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { useProjectStore } from '@/stores/project'
import SectionEditor from '@/components/workspace/SectionEditor.vue'
import TaskProgress from '@/components/common/TaskProgress.vue'
import WorkspaceView from '@/views/WorkspaceView.vue'

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: 'p1' }, query: {} }) }))

const slot = { template: '<div><slot /></div>' }
const stubs = {
  AppShell: slot, SectionEditor: false, ElTooltip: slot, ElDialog: slot,
  ReferencePanel: { template: '<div />', methods: { open() {} } },
  ElButton: { props: ['disabled'], template: '<button :disabled="disabled"><slot /></button>' },
}
const node = (content = '原正文', revision = 1) => ({ id: 's1', title: '方案', content, revision, status: 'completed' })
const project = (content = '原正文', revision = 1) => ({ id: 'p1', name: '项目', outline: [node(content, revision)] })
const deferred = () => {
  let resolve, reject
  const promise = new Promise((a, b) => { resolve = a; reject = b })
  return { promise, resolve, reject }
}
let wrappers
let pinia

function mount(component, props) {
  const wrapper = shallowMount(component, {
    props,
    global: {
      plugins: [pinia], stubs, directives: { loading: () => {} },
      config: { warnHandler(message) { if (!message.startsWith('Failed to resolve component:')) throw new Error(message) } },
    },
  })
  wrappers.push(wrapper)
  return wrapper
}

function editor() {
  const store = useProjectStore()
  store.id = 'p1'
  store.project = project()
  return mount(SectionEditor, { projectId: 'p1', node: store.findNode('s1') })
}

async function click(wrapper, label) {
  await wrapper.findAll('button').find((button) => button.text().includes(label)).trigger('click')
  await flushPromises()
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  wrappers = []
  pinia = createPinia()
  setActivePinia(pinia)
  vi.spyOn(api, 'listTasks').mockImplementation(async ({ type }) => ({ tasks: type === 'section_batch' ? [{ id: 'batch1' }] : [] }))
  vi.spyOn(api, 'getProject').mockImplementation(async () => project())
  vi.spyOn(api, 'projectProposals').mockResolvedValue({ counts: {} })
  vi.spyOn(api, 'saveSection').mockResolvedValue({ revision: 2 })
  for (const kind of ['error', 'success', 'info', 'warning']) vi.spyOn(ElMessage, kind).mockImplementation(() => {})
  vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm')
})

afterEach(async () => {
  for (const wrapper of wrappers) wrapper.unmount()
  await flushPromises()
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('批量完成与编辑器刷新', () => {
  it('防抖期间批量结束：先等待保存，再刷新项目，编辑器不重挂载', async () => {
    const saving = deferred()
    api.saveSection.mockReturnValue(saving.promise)
    const wrapper = mount(WorkspaceView)
    await flushPromises()
    const before = wrapper.getComponent(SectionEditor).vm.$.uid
    await wrapper.get('textarea').setValue('尚在防抖中的人工输入')
    api.getProject.mockResolvedValue(project('尚在防抖中的人工输入', 2))
    wrapper.getComponent(TaskProgress).vm.$emit('done', { status: 'completed', result: { generated: ['s2'] } })
    await flushPromises()
    expect(api.saveSection).toHaveBeenCalledWith('p1', 's1', '尚在防抖中的人工输入', 'completed')
    expect(api.getProject).toHaveBeenCalledTimes(1)
    saving.resolve({ revision: 2 })
    await flushPromises()
    expect(api.getProject).toHaveBeenCalledTimes(2)
    expect(wrapper.getComponent(SectionEditor).vm.$.uid).toBe(before)
    expect(wrapper.get('textarea').element.value).toBe('尚在防抖中的人工输入')
    expect(wrapper.getComponent(SectionEditor).text()).toContain('已保存')
  })

  it('保存失败后批量结束：保留草稿与未保存提示，仍能再次保存', async () => {
    api.saveSection.mockRejectedValueOnce(new Error('断网'))
    const wrapper = mount(WorkspaceView)
    await flushPromises()
    await wrapper.get('textarea').setValue('不能丢失的草稿')
    api.getProject.mockResolvedValue(project('服务器批量正文', 2))
    wrapper.getComponent(TaskProgress).vm.$emit('done', { status: 'completed', result: {} })
    await flushPromises()
    const current = wrapper.getComponent(SectionEditor)
    expect(wrapper.get('textarea').element.value).toBe('不能丢失的草稿')
    expect(current.text()).toContain('未保存')
    expect(ElMessage.error).toHaveBeenCalledWith('自动保存失败：断网')
    api.saveSection.mockResolvedValue({ revision: 3 })
    await current.vm.flushSave()
    expect(useProjectStore().findNode('s1').content).toBe('不能丢失的草稿')
    expect(current.text()).toContain('已保存')
  })

  it('刷新请求期间又输入：同章 watcher 保留新草稿', async () => {
    const loading = deferred()
    const wrapper = mount(WorkspaceView)
    await flushPromises()
    api.getProject.mockReturnValue(loading.promise)
    wrapper.getComponent(TaskProgress).vm.$emit('done', { status: 'completed', result: {} })
    await flushPromises()
    await wrapper.get('textarea').setValue('刷新过程中输入')
    loading.resolve(project('服务器正文', 2))
    await flushPromises()
    expect(wrapper.get('textarea').element.value).toBe('刷新过程中输入')
    await vi.advanceTimersByTimeAsync(1500)
    expect(api.saveSection).toHaveBeenCalledWith('p1', 's1', '刷新过程中输入', 'completed')
  })

  it('等待中的保存完成后继续保存较新的输入，不并发发送自动保存', async () => {
    const saving = deferred()
    api.saveSection.mockReturnValueOnce(saving.promise).mockResolvedValue({ revision: 3 })
    const wrapper = editor()
    await wrapper.get('textarea').setValue('第一版')
    await vi.advanceTimersByTimeAsync(1500)
    await wrapper.get('textarea').setValue('第二版')
    const flushed = wrapper.vm.flushSave()
    expect(api.saveSection).toHaveBeenCalledTimes(1)
    saving.resolve({ revision: 2 })
    await flushed
    expect(api.saveSection).toHaveBeenCalledTimes(2)
    expect(useProjectStore().findNode('s1').content).toBe('第二版')
    expect(wrapper.text()).toContain('已保存')
  })

  it.each(['第二版', '原正文'])('在途保存期间输入 %s，无需 flush 也会串行自动保存', async (latest) => {
    const saving = deferred()
    api.saveSection.mockReturnValueOnce(saving.promise).mockResolvedValue({ revision: 3 })
    const wrapper = editor()
    await wrapper.get('textarea').setValue('第一版')
    await vi.advanceTimersByTimeAsync(1500)
    await wrapper.get('textarea').setValue(latest)
    await vi.advanceTimersByTimeAsync(2000)
    expect(api.saveSection).toHaveBeenCalledTimes(1)
    saving.resolve({ revision: 2 })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(3000)
    expect(api.saveSection.mock.calls.map((call) => call[2])).toEqual(['第一版', latest])
    expect(useProjectStore().findNode('s1')).toMatchObject({ content: latest, revision: 3 })
    expect(wrapper.get('textarea').element.value).toBe(latest)
    expect(wrapper.text()).toContain('已保存')
  })

  it('在途保存期间改回原文并卸载，仍保存最后一次人工操作', async () => {
    const saving = deferred()
    api.saveSection.mockReturnValueOnce(saving.promise).mockResolvedValue({ revision: 3 })
    const wrapper = editor()
    await wrapper.get('textarea').setValue('第一版')
    await vi.advanceTimersByTimeAsync(1500)
    await wrapper.get('textarea').setValue('原正文')
    wrapper.unmount()
    wrappers = []
    saving.resolve({ revision: 2 })
    await flushPromises()
    expect(api.saveSection.mock.calls.map((call) => call[2])).toEqual(['第一版', '原正文'])
    expect(useProjectStore().findNode('s1')).toMatchObject({ content: '原正文', revision: 3 })
  })

  it('还原内容的后续保存失败时保留草稿，不循环重试，允许手动补存', async () => {
    const saving = deferred()
    api.saveSection.mockReturnValueOnce(saving.promise).mockRejectedValueOnce(new Error('断网'))
    const wrapper = editor()
    await wrapper.get('textarea').setValue('第一版')
    await vi.advanceTimersByTimeAsync(1500)
    await wrapper.get('textarea').setValue('原正文')
    saving.resolve({ revision: 2 })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.saveSection).toHaveBeenCalledTimes(2)
    expect(wrapper.get('textarea').element.value).toBe('原正文')
    expect(wrapper.text()).toContain('未保存')
    expect(ElMessage.error).toHaveBeenCalledWith('自动保存失败：断网')
    api.saveSection.mockResolvedValue({ revision: 3 })
    await wrapper.vm.flushSave()
    expect(useProjectStore().findNode('s1')).toMatchObject({ content: '原正文', revision: 3 })
    expect(wrapper.text()).toContain('已保存')
  })
})

describe('润色与单章生成的并发保护', () => {
  it('润色前保存草稿，并携带保存返回的修订号', async () => {
    vi.spyOn(api, 'polishSection').mockResolvedValue({ polished_content: '润色稿', revision: 3 })
    const wrapper = editor()
    await wrapper.get('textarea').setValue('润色的人工基稿')
    await click(wrapper, '降 AI 味')
    expect(api.polishSection).toHaveBeenCalledWith('p1', expect.objectContaining({ content: '润色的人工基稿', base_revision: 2 }))
    expect(wrapper.get('textarea').element.value).toBe('润色稿')
    expect(useProjectStore().findNode('s1').revision).toBe(3)
  })

  it('润色返回时新输入尚未自动保存：保留新输入并继续保存', async () => {
    const polishing = deferred()
    vi.spyOn(api, 'polishSection').mockReturnValue(polishing.promise)
    const wrapper = editor()
    await click(wrapper, '降 AI 味')
    await wrapper.get('textarea').setValue('润色期间的新输入')
    polishing.resolve({ polished_content: '旧稿的润色结果', revision: 2 })
    await flushPromises()
    expect(wrapper.get('textarea').element.value).toBe('润色期间的新输入')
    expect(ElMessage.info).toHaveBeenCalledWith(expect.stringContaining('历史版本'))
    expect(wrapper.text()).toContain('未保存')
    api.saveSection.mockResolvedValue({ revision: 3 })
    await vi.advanceTimersByTimeAsync(1500)
    expect(api.saveSection).toHaveBeenCalledWith('p1', 's1', '润色期间的新输入', 'completed')
    expect(wrapper.text()).toContain('已保存')
  })

  it('润色期间已保存新输入：409 保留人工稿并提示候选稿', async () => {
    const polishing = deferred()
    vi.spyOn(api, 'polishSection').mockReturnValue(polishing.promise)
    const wrapper = editor()
    await click(wrapper, '降 AI 味')
    await wrapper.get('textarea').setValue('已保存的新正文')
    await vi.advanceTimersByTimeAsync(1500)
    polishing.reject(Object.assign(new Error('正文已变化；AI 结果已保存为候选稿'), {
      status: 409, detail: { proposal_id: 'prop1', current_content: '已保存的新正文', current_revision: 2, current_status: 'completed' },
    }))
    await flushPromises()
    expect(wrapper.get('textarea').element.value).toBe('已保存的新正文')
    expect(wrapper.emitted('proposals-changed')).toHaveLength(1)
    expect(wrapper.text()).toContain('已保存')
  })

  it('迟到的旧 409 不覆盖已经保存的还原操作，也不回写旧正文', async () => {
    const polishing = deferred()
    vi.spyOn(api, 'polishSection').mockReturnValue(polishing.promise)
    api.saveSection.mockResolvedValueOnce({ revision: 2 }).mockResolvedValueOnce({ revision: 3 })
    const wrapper = editor()
    await click(wrapper, '降 AI 味')
    await wrapper.get('textarea').setValue('第一轮人工修改')
    await vi.advanceTimersByTimeAsync(1500)
    await wrapper.get('textarea').setValue('原正文')
    await vi.advanceTimersByTimeAsync(1500)
    polishing.reject(Object.assign(new Error('旧冲突响应'), {
      status: 409, detail: { proposal_id: 'prop1', current_content: '第一轮人工修改', current_revision: 2, current_status: 'completed' },
    }))
    await flushPromises()
    await vi.advanceTimersByTimeAsync(3000)
    expect(wrapper.get('textarea').element.value).toBe('原正文')
    expect(useProjectStore().findNode('s1')).toMatchObject({ content: '原正文', revision: 3 })
    expect(api.saveSection.mock.calls.map((call) => call[2])).toEqual(['第一轮人工修改', '原正文'])
    expect(wrapper.text()).toContain('已保存')
    expect(wrapper.emitted('proposals-changed')).toHaveLength(1)
    api.polishSection.mockResolvedValue({ polished_content: '下一次润色稿', revision: 4 })
    await click(wrapper, '降 AI 味')
    expect(api.polishSection).toHaveBeenLastCalledWith('p1', expect.objectContaining({ content: '原正文', base_revision: 3 }))
  })

  it('项目刷新得到较新修订后，即使正文相同也不能采用旧 409', async () => {
    const polishing = deferred()
    vi.spyOn(api, 'polishSection').mockReturnValue(polishing.promise)
    const wrapper = editor()
    await click(wrapper, '降 AI 味')
    const store = useProjectStore()
    store.project = project('原正文', 3)
    await wrapper.setProps({ node: store.findNode('s1') })
    polishing.reject(Object.assign(new Error('旧冲突响应'), {
      status: 409, detail: { proposal_id: 'prop1', current_content: '另一页的旧修改', current_revision: 2, current_status: 'completed' },
    }))
    await flushPromises()
    await vi.advanceTimersByTimeAsync(3000)
    expect(wrapper.get('textarea').element.value).toBe('原正文')
    expect(store.findNode('s1')).toMatchObject({ content: '原正文', revision: 3 })
    expect(api.saveSection).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('已保存')
  })

  it('还原操作仍在保存途中时，409 也不能替换人工输入', async () => {
    const polishing = deferred()
    const saving = deferred()
    vi.spyOn(api, 'polishSection').mockReturnValue(polishing.promise)
    api.saveSection.mockResolvedValueOnce({ revision: 2 }).mockReturnValueOnce(saving.promise)
    const wrapper = editor()
    await click(wrapper, '降 AI 味')
    await wrapper.get('textarea').setValue('第一轮人工修改')
    await vi.advanceTimersByTimeAsync(1500)
    await wrapper.get('textarea').setValue('原正文')
    await vi.advanceTimersByTimeAsync(1500)
    polishing.reject(Object.assign(new Error('正文已变化'), {
      status: 409, detail: { proposal_id: 'prop1', current_content: '第一轮人工修改', current_revision: 2, current_status: 'completed' },
    }))
    await flushPromises()
    expect(wrapper.get('textarea').element.value).toBe('原正文')
    saving.resolve({ revision: 3 })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(3000)
    expect(api.saveSection).toHaveBeenCalledTimes(2)
    expect(useProjectStore().findNode('s1')).toMatchObject({ content: '原正文', revision: 3 })
    expect(wrapper.text()).toContain('已保存')
  })

  it('润色期间修改后还原、尚未保存时，成功响应也保留人工还原', async () => {
    const polishing = deferred()
    vi.spyOn(api, 'polishSection').mockReturnValue(polishing.promise)
    api.saveSection.mockResolvedValue({ revision: 3 })
    const wrapper = editor()
    await click(wrapper, '降 AI 味')
    await wrapper.get('textarea').setValue('短暂的修改')
    await wrapper.get('textarea').setValue('原正文')
    polishing.resolve({ polished_content: 'AI 润色稿', revision: 2 })
    await flushPromises()
    expect(wrapper.get('textarea').element.value).toBe('原正文')
    await vi.advanceTimersByTimeAsync(3000)
    expect(api.saveSection).toHaveBeenCalledTimes(1)
    expect(api.saveSection).toHaveBeenCalledWith('p1', 's1', '原正文', 'completed')
    expect(useProjectStore().findNode('s1')).toMatchObject({ content: '原正文', revision: 3 })
    expect(wrapper.text()).toContain('已保存')
  })

  it('流式生成冲突：展示服务器人工稿，不把原稿重新自动保存回去', async () => {
    let onEvent, onClose
    vi.spyOn(api, 'streamSection').mockImplementation((id, body, event, error, close) => {
      onEvent = event; onClose = close
      return () => close()
    })
    const wrapper = editor()
    await click(wrapper, 'AI 撰写本节')
    expect(api.streamSection.mock.calls[0][1].base_revision).toBe(1)
    onEvent({ token: '生成中的AI正文' })
    await nextTick()
    onEvent({ done: false, status_code: 409, error: '正文已变化，已存候选稿', current_content: '另一页已校审的正文',
      current_revision: 2, current_status: 'reviewed', proposal_id: 'prop1' })
    onClose()
    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.get('textarea').element.value).toBe('另一页已校审的正文')
    expect(api.saveSection).not.toHaveBeenCalled()
    expect(useProjectStore().findNode('s1').status).toBe('reviewed')
    expect(wrapper.emitted('proposals-changed')).toHaveLength(1)
  })
})
