<template>
  <aside
    v-show="open"
    class="research-copilot-panel"
    role="complementary"
    aria-label="AI 研究助手"
    @keydown.esc="closePanel"
  >
    <header class="copilot-header">
      <div class="copilot-title">
        <Bot :size="18" aria-hidden="true" />
        <span>AI 研究助手</span>
      </div>
      <button
        type="button"
        class="copilot-icon-button"
        title="关闭研究助手"
        aria-label="关闭研究助手"
        @click="closePanel"
      >
        <X :size="18" />
      </button>
    </header>

    <section class="copilot-context" aria-label="当前研究上下文">
      <div class="context-row">
        <Database :size="15" aria-hidden="true" />
        <span class="context-label">知识库</span>
        <strong :title="knowledgeBaseLabel">{{ knowledgeBaseLabel }}</strong>
      </div>
      <div class="context-row">
        <FolderKanban :size="15" aria-hidden="true" />
        <span class="context-label">项目</span>
        <strong :title="projectLabel">{{ projectLabel }}</strong>
      </div>
      <div class="context-row">
        <PanelTop :size="15" aria-hidden="true" />
        <span class="context-label">工作区</span>
        <strong>{{ surfaceConfig.label }}</strong>
      </div>
      <div v-if="selection?.title" class="context-row">
        <MousePointer2 :size="15" aria-hidden="true" />
        <span class="context-label">当前对象</span>
        <strong :title="selection.title">{{ selection.title }}</strong>
      </div>
    </section>

    <div v-if="statusMessage" class="copilot-status" :class="{ 'is-error': ensureError }">
      <LoaderCircle v-if="ensuring" :size="15" class="status-spinner" aria-hidden="true" />
      <span>{{ statusMessage }}</span>
      <button
        v-if="ensureError && kbId"
        type="button"
        class="retry-button"
        :disabled="ensuring"
        @click="ensureThread"
      >
        <RefreshCw :size="14" />
        重试
      </button>
    </div>

    <div class="copilot-chat">
      <AgentChatComponent
        v-if="agentId"
        :agent-id="agentId"
        :single-mode="true"
        :initial-thread-id="threadId"
        :greeting="copilotGreeting"
        :starter-prompts="starterPrompts"
        :show-tool-approval-mode="false"
        :send-disabled="ensuring || !kbId || !threadId || Boolean(ensureError)"
        @run-complete="emit('run-complete', $event)"
      />
      <div v-else class="copilot-empty">
        <LoaderCircle v-if="ensuring" :size="22" class="status-spinner" aria-hidden="true" />
        <Bot v-else :size="24" aria-hidden="true" />
        <span>{{ ensureError || (kbId ? '研究助手尚未就绪' : '请先选择知识库') }}</span>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  Bot,
  Database,
  FolderKanban,
  LoaderCircle,
  MousePointer2,
  PanelTop,
  RefreshCw,
  X
} from 'lucide-vue-next'
import AgentChatComponent from '@/components/AgentChatComponent.vue'
import { researchApi } from '@/apis/research_api'
import { useChatThreadsStore } from '@/stores/chatThreads'

const SURFACE_CONFIG = {
  projects: {
    label: '研究项目',
    starters: [
      { label: '规划下一步', prompt: '请检查当前项目进度，规划下一步并更新对应任务。' },
      { label: '检查项目风险', prompt: '请检查当前项目的阻塞项、证据缺口和进度风险。' }
    ]
  },
  library: {
    label: '文献库',
    starters: [
      { label: '梳理论文证据', prompt: '请梳理当前文献库中的关键证据和主要结论。' },
      { label: '补充项目成果', prompt: '请找出适合加入当前项目的文献，并说明选择依据。' }
    ]
  },
  search: {
    label: '学术检索',
    starters: [
      { label: '优化检索策略', prompt: '请根据当前研究问题优化检索策略，并执行必要的补充检索。' },
      { label: '评估检索结果', prompt: '请评估当前检索结果的相关性、覆盖度和证据缺口。' }
    ]
  },
  synthesis: {
    label: '证据综述',
    starters: [
      { label: '生成证据综述', prompt: '请基于当前证据生成一份结构化综述。' },
      { label: '检查证据冲突', prompt: '请找出当前综述中的证据冲突、薄弱结论和缺失引用。' }
    ]
  },
  graph: {
    label: '学术图谱',
    starters: [
      { label: '分析关系网络', prompt: '请分析当前学术图谱中的关键文献、作者和关系网络。' },
      { label: '定位关键文献', prompt: '请从当前图谱中定位最值得深入阅读的关键文献并说明原因。' }
    ]
  },
  trends: {
    label: '研究趋势',
    starters: [
      { label: '总结研究趋势', prompt: '请总结当前领域的主要研究趋势及其证据。' },
      { label: '识别趋势变化', prompt: '请识别当前领域近期的重要趋势变化和潜在原因。' }
    ]
  },
  opportunities: {
    label: '研究机会',
    starters: [
      { label: '评估研究机会', prompt: '请评估当前研究机会的价值、可行性和证据基础。' },
      { label: '转为项目计划', prompt: '请把最有价值的研究机会转化为可执行的项目计划。' }
    ]
  },
  'analysis-evaluations': {
    label: '分析评测',
    starters: [
      { label: '比较分析结果', prompt: '请比较当前论文分析结果，指出一致结论和显著分歧。' },
      { label: '定位低置信项', prompt: '请定位当前评测中的低置信结论，并提出复核步骤。' }
    ]
  },
  'user-studies': {
    label: '用户评测',
    starters: [
      { label: '设计评测任务', prompt: '请根据当前研究目标设计一组可执行的用户评测任务。' },
      { label: '总结用户反馈', prompt: '请总结当前用户评测反馈，并转化为改进建议。' }
    ]
  }
}

const props = defineProps({
  open: { type: Boolean, default: false },
  kbId: { type: String, default: '' },
  kbName: { type: String, default: '' },
  projectId: { type: String, default: '' },
  projectTitle: { type: String, default: '' },
  surface: { type: String, default: 'projects' },
  selection: { type: Object, default: null }
})
const emit = defineEmits(['update:open', 'run-complete'])

const chatThreadsStore = useChatThreadsStore()
const agentId = ref('')
const threadId = ref('')
const ensuring = ref(false)
const ensureError = ref('')
let ensureGeneration = 0
let pendingEnsureRequest = null
let ensureLoopPromise = null

const surfaceConfig = computed(() => SURFACE_CONFIG[props.surface] || SURFACE_CONFIG.projects)
const knowledgeBaseLabel = computed(() => props.kbName || props.kbId || '未选择')
const projectLabel = computed(() =>
  props.projectId ? props.projectTitle || props.projectId : '知识库级研究'
)
const copilotGreeting = computed(() =>
  `正在处理“${knowledgeBaseLabel.value}”的${surfaceConfig.value.label}工作。`
)
const starterPrompts = computed(() => {
  const focus = props.selection?.title
    ? `请围绕当前选择“${props.selection.title}”处理：`
    : ''
  return surfaceConfig.value.starters.map((starter) => ({
    ...starter,
    prompt: `${focus}${starter.prompt}`
  }))
})
const contextPayload = computed(() => ({
  kb_id: props.kbId,
  project_id: props.projectId || null,
  surface: props.surface,
  selection: props.selection
    ? {
        type: props.selection.type,
        id: props.selection.id,
        title: props.selection.title || ''
      }
    : null
}))
const contextSignature = computed(() => JSON.stringify(contextPayload.value))
const statusMessage = computed(() => {
  if (!props.kbId) return '请先选择知识库'
  if (ensuring.value) return threadId.value ? '正在切换研究上下文…' : '正在连接研究助手…'
  return ensureError.value
})

const runEnsureLoop = async () => {
  while (pendingEnsureRequest) {
    const { generation, payload } = pendingEnsureRequest
    pendingEnsureRequest = null
    try {
      const result = await researchApi.ensureCopilotThread(payload)
      if (generation !== ensureGeneration) continue
      const nextAgentId = result?.agent_id || result?.agentId
      const nextThread = result?.thread
      if (!nextAgentId || !nextThread?.id) {
        throw new Error('研究助手会话响应无效')
      }
      chatThreadsStore.upsertThread(nextThread)
      agentId.value = nextAgentId
      threadId.value = nextThread.id
    } catch (error) {
      if (generation === ensureGeneration) {
        ensureError.value = error.message || '研究助手连接失败'
      }
    }
  }
}

const startEnsureLoop = () => {
  if (ensureLoopPromise) return ensureLoopPromise
  const loopPromise = runEnsureLoop()
  ensureLoopPromise = loopPromise
  void loopPromise.finally(() => {
    if (ensureLoopPromise !== loopPromise) return
    ensureLoopPromise = null
    if (pendingEnsureRequest) {
      void startEnsureLoop()
    } else {
      ensuring.value = false
    }
  })
  return loopPromise
}

const ensureThread = () => {
  if (!props.kbId) return
  pendingEnsureRequest = {
    generation: ++ensureGeneration,
    payload: contextPayload.value
  }
  ensuring.value = true
  ensureError.value = ''
  return startEnsureLoop()
}

const closePanel = () => emit('update:open', false)

watch(
  [() => props.open, contextSignature],
  ([isOpen, signature], previousValues = []) => {
    const previousSignature = previousValues[1]
    if (!props.kbId) {
      ensureGeneration += 1
      pendingEnsureRequest = null
      ensuring.value = false
      ensureError.value = ''
      return
    }
    if (isOpen || (previousSignature !== undefined && signature !== previousSignature)) {
      void ensureThread()
    }
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  ensureGeneration += 1
  pendingEnsureRequest = null
})
</script>

<style lang="less" scoped>
.research-copilot-panel {
  position: absolute;
  inset: 0 0 0 auto;
  z-index: 40;
  width: var(--research-copilot-width, 420px);
  min-width: 320px;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  overflow: hidden;
  border-left: 1px solid var(--gray-150);
  background: var(--gray-0);
  box-shadow: -8px 0 24px var(--shadow-1);
}

.copilot-header {
  min-height: 48px;
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 12px 0 16px;
  border-bottom: 1px solid var(--gray-150);
}

.copilot-title {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text);
  font-size: 15px;
  font-weight: 600;
}

.copilot-title svg {
  flex: 0 0 auto;
  color: var(--main-600);
}

.copilot-icon-button {
  width: 36px;
  height: 36px;
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  border-radius: 6px;
  color: var(--gray-600);
  background: transparent;
  cursor: pointer;
}

.copilot-icon-button:hover {
  color: var(--color-text);
  background: var(--gray-50);
}

.copilot-icon-button:focus-visible,
.retry-button:focus-visible {
  outline: 2px solid var(--main-500);
  outline-offset: 2px;
}

.copilot-context {
  display: grid;
  flex: 0 0 auto;
  gap: 6px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-25);
}

.context-row {
  min-width: 0;
  display: grid;
  grid-template-columns: 16px 54px minmax(0, 1fr);
  gap: 7px;
  align-items: center;
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 20px;
}

.context-row svg {
  color: var(--gray-500);
}

.context-row strong {
  min-width: 0;
  overflow: hidden;
  color: var(--color-text-secondary);
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.context-label {
  white-space: nowrap;
}

.copilot-status {
  min-height: 34px;
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 7px;
  padding: 6px 12px 6px 16px;
  border-bottom: 1px solid var(--gray-150);
  color: var(--color-text-tertiary);
  background: var(--gray-10);
  font-size: 12px;
}

.copilot-status.is-error {
  color: var(--color-error-700);
  background: var(--color-error-50);
}

.copilot-status span {
  min-width: 0;
  flex: 1;
  overflow-wrap: anywhere;
}

.status-spinner {
  flex: 0 0 auto;
  animation: copilot-spin 1s linear infinite;
}

.retry-button {
  min-height: 28px;
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  border: 1px solid currentColor;
  border-radius: 6px;
  color: inherit;
  background: transparent;
  cursor: pointer;
  font-size: 12px;
}

.retry-button:disabled {
  opacity: 0.55;
  cursor: wait;
}

.copilot-chat {
  min-height: 0;
  flex: 1;
}

.copilot-empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 24px;
  box-sizing: border-box;
  color: var(--color-text-tertiary);
  font-size: 13px;
  text-align: center;
}

.copilot-empty svg:not(.status-spinner) {
  color: var(--gray-400);
}

@keyframes copilot-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 960px) {
  .research-copilot-panel {
    position: fixed;
    inset: 0 0 0 auto;
    z-index: 1100;
    width: min(var(--research-copilot-width, 420px), 100vw);
    min-width: 0;
    box-shadow: -12px 0 28px var(--shadow-3);
  }
}

@media (max-width: 768px) {
  .research-copilot-panel {
    width: 100vw;
  }
}

@media (max-width: 390px) {
  .copilot-header {
    padding-right: 8px;
    padding-left: 12px;
  }

  .copilot-context {
    padding-right: 12px;
    padding-left: 12px;
  }

  .context-row {
    grid-template-columns: 16px 48px minmax(0, 1fr);
    gap: 6px;
  }
}
</style>
