import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createRenderer, h, nextTick, reactive, ssrContextKey } from 'vue'
import { createServer } from 'vite'

const webRoot = fileURLToPath(new URL('../..', import.meta.url))

const insertNode = (child, parent, anchor = null) => {
  child.parent = parent
  const index = anchor ? parent.children.indexOf(anchor) : -1
  if (index === -1) parent.children.push(child)
  else parent.children.splice(index, 0, child)
}

const renderer = createRenderer({
  patchProp: (element, key, _previousValue, value) => { element.props[key] = value },
  insert: insertNode,
  remove: (child) => {
    const index = child.parent?.children.indexOf(child) ?? -1
    if (index !== -1) child.parent.children.splice(index, 1)
    child.parent = null
  },
  createElement: (type) => ({ type, children: [], props: {}, parent: null }),
  createText: (text) => ({ text, children: [], parent: null }),
  createComment: (text) => ({ comment: text, children: [], parent: null }),
  setText: (node, text) => { node.text = text },
  setElementText: (node, text) => {
    node.children = []
    node.text = text
  },
  parentNode: (node) => node.parent,
  nextSibling: (node) => {
    const siblings = node.parent?.children || []
    return siblings[siblings.indexOf(node) + 1] || null
  },
  querySelector: () => null,
  setScopeId: () => {},
  cloneNode: (node) => ({ ...node, children: [...node.children], parent: null }),
  insertStaticContent: (content, parent, anchor) => {
    const node = { text: content, children: [], parent: null }
    insertNode(node, parent, anchor)
    return [node, node]
  }
})

const testModuleStubs = {
  name: 'research-component-test-stubs',
  enforce: 'pre',
  resolveId(source) {
    const normalizedSource = source.replaceAll('\\', '/')
    if (source === 'ant-design-vue') return '\0ant-design-vue-stub'
    if (normalizedSource.endsWith('/components/AgentChatComponent.vue')) return '\0agent-chat-stub'
    if (/\/stores\/chatThreads(?:\.js)?$/.test(normalizedSource)) return '\0chat-threads-stub'
    if (/\/stores\/user(?:\.js)?$/.test(normalizedSource)) return '\0user-store-stub'
  },
  load(id) {
    if (id === '\0ant-design-vue-stub') {
      return 'export const message = { success() {}, error() {}, warning() {} }'
    }
    if (id === '\0agent-chat-stub') return 'export default { render: () => null }'
    if (id === '\0chat-threads-stub') {
      return 'export const useChatThreadsStore = () => ({ upsertThread() {} })'
    }
    if (id === '\0user-store-stub') return 'export const useUserStore = () => ({ isAdmin: false })'
  }
}

let viteServer
let researchApi
let CopilotPanel
let ProjectWorkspace
let ProjectPlan

before(async () => {
  viteServer = await createServer({
    root: webRoot,
    appType: 'custom',
    logLevel: 'silent',
    plugins: [testModuleStubs],
    server: { middlewareMode: true }
  })
  ;({ researchApi } = await viteServer.ssrLoadModule('/src/apis/research_api.js'))
  researchApi.listProjectTemplates = async () => ({ items: [] })
  ;({ default: CopilotPanel } = await viteServer.ssrLoadModule(
    '/src/components/research/ResearchCopilotPanel.vue'
  ))
  ;({ default: ProjectWorkspace } = await viteServer.ssrLoadModule(
    '/src/components/research/ResearchProjectWorkspace.vue'
  ))
  ;({ default: ProjectPlan } = await viteServer.ssrLoadModule(
    '/src/components/research/ResearchProjectPlan.vue'
  ))
})

after(async () => {
  await viteServer.close()
})

const mountComponent = (component, initialProps) => {
  const props = reactive({ ...initialProps })
  const mountedComponent = { ...component, render: () => null }
  const Root = { render: () => h(mountedComponent, props) }
  const app = renderer.createApp(Root)
  app.config.warnHandler = () => {}
  app.provide(ssrContextKey, { modules: new Set() })
  app.mount({ type: 'root', children: [], props: {}, parent: null })
  return {
    app,
    props,
    instance: () => app._instance.subTree.component,
    unmount: () => app.unmount()
  }
}

const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

const waitFor = async (condition) => {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (condition()) return
    await nextTick()
    await Promise.resolve()
  }
  assert.fail('condition was not reached')
}

test('Copilot ensure requests are serialized and intermediate contexts are coalesced', async () => {
  const originalEnsure = researchApi.ensureCopilotThread
  const requests = []
  let activeRequests = 0
  let maxActiveRequests = 0
  researchApi.ensureCopilotThread = (payload) => {
    const response = deferred()
    activeRequests += 1
    maxActiveRequests = Math.max(maxActiveRequests, activeRequests)
    const requestPromise = response.promise.finally(() => { activeRequests -= 1 })
    requests.push({
      payload: { ...payload, selection: payload.selection ? { ...payload.selection } : null },
      response,
      requestPromise
    })
    return requestPromise
  }

  const mounted = mountComponent(CopilotPanel, {
    open: true,
    kbId: 'kb-1',
    surface: 'projects',
    selection: { type: 'paper', id: 'selection-a', title: 'A' }
  })

  try {
    assert.equal(requests.length, 1)

    mounted.props.surface = 'library'
    mounted.props.selection = { type: 'paper', id: 'selection-b', title: 'B' }
    await nextTick()
    assert.equal(requests.length, 1)

    mounted.props.surface = 'search'
    mounted.props.selection = { type: 'paper', id: 'selection-c', title: 'C' }
    await nextTick()
    assert.equal(requests.length, 1)

    requests[0].response.resolve({ agent_id: 'agent-a', thread: { id: 'thread-a' } })
    await waitFor(() => requests.length === 2)

    assert.equal(requests[1].payload.surface, 'search')
    assert.deepEqual(requests[1].payload.selection, { type: 'paper', id: 'selection-c', title: 'C' })
    assert.equal(maxActiveRequests, 1)

    requests[1].response.resolve({ agent_id: 'agent-c', thread: { id: 'thread-c' } })
    await requests[1].requestPromise
    void mounted.instance().setupState.ensureThread()
    await waitFor(() => requests.length === 3)
    requests[2].response.resolve({ agent_id: 'agent-c', thread: { id: 'thread-c' } })
    await waitFor(() => mounted.instance().setupState.ensuring === false)

    assert.equal(maxActiveRequests, 1)
    assert.equal(mounted.instance().setupState.agentId, 'agent-c')
    assert.equal(mounted.instance().setupState.threadId, 'thread-c')
  } finally {
    mounted.unmount()
    researchApi.ensureCopilotThread = originalEnsure
  }
})

test('switching projects clears the published project before the next detail resolves', async () => {
  const originalGetProject = researchApi.getProject
  const originalListAssets = researchApi.listProjectAssets
  const projectResponse = deferred()
  const emittedProjects = []
  researchApi.getProject = () => projectResponse.promise
  researchApi.listProjectAssets = async () => ({ items: [], total: 0 })

  const mounted = mountComponent(ProjectWorkspace, {
    kbId: '',
    onProjectChange: (project) => emittedProjects.push(project)
  })

  try {
    mounted.instance().setupState.selectedProjectId = 'project-a'
    mounted.instance().setupState.projectDetail = { project_id: 'project-a', title: 'A' }

    const switching = mounted.instance().setupState.selectProject('project-b')

    assert.equal(mounted.instance().setupState.projectDetail, null)
    assert.deepEqual(emittedProjects, [null])

    const projectB = { project_id: 'project-b', title: 'B' }
    projectResponse.resolve(projectB)
    await switching
    assert.deepEqual(emittedProjects, [null, projectB])
  } finally {
    mounted.unmount()
    researchApi.getProject = originalGetProject
    researchApi.listProjectAssets = originalListAssets
  }
})

test('list-driven project changes also clear the published project before loading details', async () => {
  const originalListProjects = researchApi.listProjects
  const originalGetProject = researchApi.getProject
  const originalListAssets = researchApi.listProjectAssets
  const projectsResponse = deferred()
  const projectResponse = deferred()
  const emittedProjects = []
  let detailRequests = 0
  researchApi.listProjects = () => projectsResponse.promise
  researchApi.getProject = () => {
    detailRequests += 1
    return projectResponse.promise
  }
  researchApi.listProjectAssets = async () => ({ items: [], total: 0 })

  const mounted = mountComponent(ProjectWorkspace, {
    kbId: 'kb-1',
    onProjectChange: (project) => emittedProjects.push(project)
  })

  try {
    mounted.instance().setupState.selectedProjectId = 'project-a'
    mounted.instance().setupState.projectDetail = { project_id: 'project-a', title: 'A' }

    projectsResponse.resolve({ items: [{ project_id: 'project-b', title: 'B' }], total: 1 })
    await waitFor(() => detailRequests === 1)

    assert.equal(mounted.instance().setupState.projectDetail, null)
    assert.deepEqual(emittedProjects, [null])

    const projectB = { project_id: 'project-b', title: 'B' }
    projectResponse.resolve(projectB)
    await waitFor(() => emittedProjects.length === 2)
    assert.deepEqual(emittedProjects, [null, projectB])
  } finally {
    mounted.unmount()
    researchApi.listProjects = originalListProjects
    researchApi.getProject = originalGetProject
    researchApi.listProjectAssets = originalListAssets
  }
})

test('project plan exposes an explicit refresh operation', async () => {
  const originalGetPlan = researchApi.getProjectPlan
  let planRequests = 0
  const project = {
    project_id: 'project-a',
    title: 'Project A',
    status: 'active',
    research_question: 'Question',
    progress: 0,
    health: 'on_track',
    tags: [],
    asset_counts: {},
    activity_log: []
  }
  researchApi.getProjectPlan = async () => {
    planRequests += 1
    return {
      summary: {
        progress: 0,
        next_due_date: null,
        tasks: { open: 0, in_progress: 0, blocked: 0, overdue: 0 },
        milestones: { overdue: 0 }
      },
      milestones: [],
      unassigned_tasks: []
    }
  }

  const mounted = mountComponent(ProjectPlan, { project })

  try {
    await waitFor(() => planRequests === 1)
    await mounted.instance().exposed.refresh()
    await waitFor(() => planRequests === 2)
    assert.equal(planRequests, 2)
  } finally {
    mounted.unmount()
    researchApi.getProjectPlan = originalGetPlan
  }
})

test('project plan dependency editor excludes the current task and persists changes', async () => {
  const originalGetPlan = researchApi.getProjectPlan
  const originalCreateDependency = researchApi.createProjectTaskDependency
  const originalDeleteDependency = researchApi.deleteProjectTaskDependency
  const created = []
  const deleted = []
  const project = { project_id: 'project-a', status: 'active' }
  const taskA = { task_id: 'task-a', title: '设计实验' }
  const taskB = { task_id: 'task-b', title: '整理数据' }
  const taskC = { task_id: 'task-c', title: '撰写论文' }
  researchApi.getProjectPlan = async () => ({
    summary: { progress: 0, next_due_date: null, tasks: {}, milestones: {} },
    milestones: [{ milestone_id: 'milestone-a', title: '阶段一', tasks: [taskA, taskB] }],
    unassigned_tasks: [taskC],
    task_dependencies: [{
      dependency_id: 'dependency-1', task_id: 'task-c', depends_on_task_id: 'task-a'
    }]
  })
  researchApi.createProjectTaskDependency = async (projectId, payload) => {
    created.push({ projectId, payload })
    return { dependency_id: 'dependency-2', ...payload }
  }
  researchApi.deleteProjectTaskDependency = async (projectId, dependencyId) => {
    deleted.push({ projectId, dependencyId })
  }

  const mounted = mountComponent(ProjectPlan, { project })
  try {
    await waitFor(() => mounted.instance().setupState.plan?.task_dependencies?.length === 1)
    mounted.instance().setupState.openDependencyModal(taskC)
    assert.deepEqual(mounted.instance().setupState.dependencyOptions, [
      { value: 'task-a', label: '设计实验' },
      { value: 'task-b', label: '整理数据' }
    ])
    assert.deepEqual(mounted.instance().setupState.selectedDependencyTaskIds, ['task-a'])

    mounted.instance().setupState.selectedDependencyTaskIds = ['task-b']
    await mounted.instance().setupState.saveDependencies()
    assert.deepEqual(deleted, [{ projectId: 'project-a', dependencyId: 'dependency-1' }])
    assert.deepEqual(created, [{
      projectId: 'project-a',
      payload: { task_id: 'task-c', depends_on_task_id: 'task-b' }
    }])
    assert.equal(mounted.instance().setupState.dependencyModalOpen, false)
  } finally {
    mounted.unmount()
    researchApi.getProjectPlan = originalGetPlan
    researchApi.createProjectTaskDependency = originalCreateDependency
    researchApi.deleteProjectTaskDependency = originalDeleteDependency
  }
})

test('project plan dependency editor keeps modal open when saving fails', async () => {
  const originalGetPlan = researchApi.getProjectPlan
  const originalCreateDependency = researchApi.createProjectTaskDependency
  const project = { project_id: 'project-a', status: 'active' }
  const task = { task_id: 'task-a', title: '设计实验' }
  researchApi.getProjectPlan = async () => ({
    summary: { progress: 0, next_due_date: null, tasks: {}, milestones: {} },
    milestones: [{ milestone_id: 'milestone-a', title: '阶段一', tasks: [task] }],
    unassigned_tasks: [],
    task_dependencies: []
  })
  researchApi.createProjectTaskDependency = async () => { throw new Error('依赖服务不可用') }

  const mounted = mountComponent(ProjectPlan, { project })
  try {
    await waitFor(() => mounted.instance().setupState.plan !== null)
    mounted.instance().setupState.openDependencyModal(task)
    mounted.instance().setupState.selectedDependencyTaskIds = ['missing-task']
    await mounted.instance().setupState.saveDependencies()
    assert.equal(mounted.instance().setupState.dependencyModalOpen, true)
  } finally {
    mounted.unmount()
    researchApi.getProjectPlan = originalGetPlan
    researchApi.createProjectTaskDependency = originalCreateDependency
  }
})

test('workspace refresh invokes the current plan even when project loading keeps the same id', async () => {
  const mounted = mountComponent(ProjectWorkspace, { kbId: '' })
  let planRefreshes = 0

  try {
    mounted.instance().setupState.projectPlanRef = {
      refresh: async () => { planRefreshes += 1 }
    }

    await mounted.instance().exposed.refresh()

    assert.equal(planRefreshes, 1)
  } finally {
    mounted.unmount()
  }
})
