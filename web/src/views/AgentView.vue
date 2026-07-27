<template>
  <div class="agent-view">
    <div class="agent-view-body">
      <!-- 中间内容区域 -->
      <div class="content">
        <AgentChatComponent
          ref="chatComponentRef"
          :single-mode="false"
          :send-disabled="isResearchCopilotSelected && (!researchKbId || ensuringThread)"
          @thread-change="handleThreadChange"
        >
          <template #input-actions-left="{ hasActiveThread }">
            <a-dropdown
              v-if="selectedAgentId"
              v-model:open="agentDropdownOpen"
              :trigger="['click']"
              placement="topLeft"
              overlay-class-name="config-dropdown-overlay"
            >
              <button
                ref="agentDropdownTriggerRef"
                type="button"
                class="input-action-btn config-dropdown-trigger"
                :class="{ disabled: isLoadingConfig }"
                :aria-label="currentAgentLabel"
              >
                <FallbackAvatar
                  v-if="currentAgentOption"
                  class="config-dropdown-compact-icon"
                  :src="currentAgentOption.icon"
                  :default-src="currentAgentOption.defaultIcon"
                  :name="currentAgentOption.label"
                  :seed="currentAgentOption.value || currentAgentOption.label"
                  kind="agent"
                  :size="18"
                  shape="rounded"
                  alt=""
                />
                <span class="hide-text config-dropdown-text">{{ currentAgentLabel }}</span>
                <ChevronDown size="15" class="config-dropdown-chevron" />
              </button>

              <template #overlay>
                <div ref="agentDropdownPanelRef" class="config-dropdown-panel">
                  <button
                    v-for="agent in agentQuickSwitchOptions"
                    :key="agent.value"
                    type="button"
                    class="config-dropdown-item"
                    :class="{
                      selected: agent.value === selectedAgentId,
                      disabled: hasActiveThread && agent.value !== selectedAgentId
                    }"
                    @click="handleAgentSwitch(agent.value, hasActiveThread)"
                  >
                    <FallbackAvatar
                      class="config-dropdown-item-icon-image"
                      :src="agent.icon"
                      :default-src="agent.defaultIcon"
                      :name="agent.label"
                      :seed="agent.value || agent.label"
                      kind="agent"
                      :size="24"
                      shape="rounded"
                      :alt="`${agent.label}图标`"
                    />
                    <span class="config-dropdown-item-label">{{ agent.label }}</span>
                    <span v-if="agent.isBuiltin" class="config-dropdown-item-badge">内置</span>
                    <Check
                      v-if="agent.value === selectedAgentId"
                      :size="14"
                      class="config-dropdown-item-check"
                    />
                  </button>

                  <div v-if="hasActiveThread" class="config-dropdown-hint">
                    当前对话已绑定智能体，新对话可切换。
                  </div>

                  <div class="config-dropdown-divider"></div>

                  <button
                    type="button"
                    class="config-dropdown-item action-item"
                    @click="openAgentManagement"
                  >
                    <Settings2 :size="15" class="config-dropdown-item-icon" />
                    <span class="config-dropdown-item-label">管理智能体</span>
                  </button>
                </div>
              </template>
            </a-dropdown>

            <div v-if="isResearchCopilotSelected" class="research-context-bar">
              <Database :size="14" class="research-context-icon" aria-hidden="true" />
              <a-select
                v-model:value="researchKbId"
                :options="knowledgeBaseOptions"
                :loading="false"
                placeholder="选择知识库"
                size="small"
                :bordered="false"
                class="research-context-select"
                :disabled="ensuringThread"
                @change="handleResearchKbChange"
              />
              <a-select
                v-if="researchKbId"
                v-model:value="researchProjectId"
                :options="projectOptions"
                :loading="isLoadingProjects"
                placeholder="项目（可选）"
                size="small"
                :bordered="false"
                allow-clear
                class="research-context-select research-context-project"
                :disabled="ensuringThread"
                @change="handleResearchProjectChange"
              />
              <LoaderCircle
                v-if="ensuringThread"
                :size="13"
                class="research-context-spinner"
                aria-hidden="true"
              />
            </div>
          </template>
        </AgentChatComponent>
      </div>
    </div>
    <AgentEditModal
      ref="agentEditModalRef"
      :backend-options="agentBackendOptions"
      @saved="handleAgentSaved"
    />
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { Settings2, ChevronDown, Check, Database, LoaderCircle } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { agentApi } from '@/apis/agent_api'
import { researchApi } from '@/apis/research_api'
import { useOutsidePointerdown } from '@/composables/useOutsidePointerdown'
import AgentChatComponent from '@/components/AgentChatComponent.vue'
import AgentEditModal from '@/components/model-management/AgentEditModal.vue'
import { isBuiltinAgent, isResearchCopilotAgent, useAgentStore } from '@/stores/agent'
import { useChatThreadsStore } from '@/stores/chatThreads'
import { handleChatError } from '@/utils/errorHandler'
import { generatePixelAvatar } from '@/utils/pixelAvatar'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'

import { storeToRefs } from 'pinia'

// 组件引用
const chatComponentRef = ref(null)
const agentEditModalRef = ref(null)

// Stores
const agentStore = useAgentStore()
const chatThreadsStore = useChatThreadsStore()
const route = useRoute()
const router = useRouter()

// 从 agentStore 中获取响应式状态
const { agents, selectedAgentId, isLoadingConfig, availableKnowledgeBases } =
  storeToRefs(agentStore)

const syncingRouteThread = ref(false)

const getRouteThreadId = () => {
  const value = route.params.thread_id
  return typeof value === 'string' ? value : ''
}

const getRouteAgentId = () => {
  const value = route.query.agent_id
  return typeof value === 'string' ? value : ''
}

const syncSelectedThreadFromRoute = async () => {
  const chatComponent = chatComponentRef.value
  if (!chatComponent?.selectThreadFromRoute) return

  const threadId = getRouteThreadId()
  syncingRouteThread.value = true
  try {
    if (!threadId && !agentStore.isInitialized) {
      await agentStore.initialize()
    }

    const ok = await chatComponent.selectThreadFromRoute(threadId)
    if (threadId && !ok) {
      await router.replace({ name: 'AgentComp' })
    }
  } catch (error) {
    handleChatError(error, 'load')
  } finally {
    syncingRouteThread.value = false
  }
}

const consumeRouteAgentSelection = async () => {
  const targetAgentId = getRouteAgentId()
  if (!targetAgentId || getRouteThreadId()) return

  try {
    if (!agentStore.isInitialized) {
      await agentStore.initialize()
    }

    await nextTick()
    await chatComponentRef.value?.selectThreadFromRoute?.('')
    await agentStore.selectAgent(targetAgentId)
  } catch (error) {
    handleChatError(error, 'load')
  } finally {
    const nextQuery = { ...route.query }
    delete nextQuery.agent_id
    await router.replace({ name: 'AgentComp', query: nextQuery })
  }
}

watch(
  () => route.params.thread_id,
  async () => {
    await syncSelectedThreadFromRoute()
    if (!getRouteThreadId() && isResearchCopilotSelected.value && researchKbId.value) {
      ensureResearchThread()
    }
  },
  { immediate: true }
)

watch(
  () => route.query.agent_id,
  () => {
    consumeRouteAgentSelection()
  },
  { immediate: true }
)

watch(chatComponentRef, (instance) => {
  if (!instance) return
  syncSelectedThreadFromRoute()
})

const handleThreadChange = (threadId) => {
  if (syncingRouteThread.value) return
  const currentRouteThreadId = getRouteThreadId()
  const nextThreadId = threadId || ''
  if (currentRouteThreadId === nextThreadId) return

  if (nextThreadId) {
    router.replace({ name: 'AgentCompWithThreadId', params: { thread_id: nextThreadId } })
  } else {
    router.replace({ name: 'AgentComp' })
  }
}

const agentQuickSwitchOptions = computed(() =>
  (agents.value || [])
    .filter((agent) => !agent.is_subagent)
    .map((agent) => ({
      label: agent.name || agent.id,
      value: agent.id,
      icon: agent.icon || '',
      defaultIcon: agent.id ? generatePixelAvatar(agent.id) : '',
      isBuiltin: isBuiltinAgent(agent)
    }))
)

const currentAgentOption = computed(() =>
  agentQuickSwitchOptions.value.find((agent) => agent.value === selectedAgentId.value)
)

const currentAgentLabel = computed(() => {
  if (isLoadingConfig.value) return '加载中...'
  return currentAgentOption.value?.label || '智能体'
})

const agentDropdownOpen = ref(false)
const agentDropdownTriggerRef = ref(null)
const agentDropdownPanelRef = ref(null)
const agentBackendOptions = ref([])
const agentBackendsLoaded = ref(false)

const loadAgentBackends = async () => {
  if (agentBackendsLoaded.value) return
  const response = await agentApi.getAgentBackends()
  agentBackendOptions.value = (response.backends || []).map((backend) => ({
    label: backend.name || backend.backend_id,
    value: backend.backend_id
  }))
  agentBackendsLoaded.value = true
}

const handleAgentSwitch = async (agentId, hasActiveThread) => {
  if (!agentId || agentId === selectedAgentId.value) return
  if (hasActiveThread) {
    message.info('当前对话已绑定智能体，请新建对话后切换')
    return
  }
  try {
    await agentStore.selectAgent(agentId)
    agentDropdownOpen.value = false
  } catch (error) {
    console.error('切换智能体出错:', error)
    message.error('切换智能体失败')
  }
}

const handleAgentSaved = async () => {
  await agentStore.fetchAgents()
  if (selectedAgentId.value) {
    await agentStore.fetchAgentDetail(selectedAgentId.value, true)
  }
}

const openAgentManagement = async () => {
  agentDropdownOpen.value = false
  if (!selectedAgentId.value) {
    message.warning('请先选择智能体')
    return
  }
  try {
    await loadAgentBackends()
    await agentEditModalRef.value?.openEdit(selectedAgentId.value)
  } catch (error) {
    message.error(error.message || '打开智能体配置失败')
  }
}

// ==================== Research Copilot 上下文 ====================
const isResearchCopilotSelected = computed(() =>
  isResearchCopilotAgent({ id: selectedAgentId.value })
)

const researchKbId = ref('')
const researchProjectId = ref(undefined)
const researchProjects = ref([])
const isLoadingProjects = ref(false)
const ensuringThread = ref(false)
let ensureGeneration = 0

const knowledgeBaseOptions = computed(() =>
  (availableKnowledgeBases.value || []).map((kb) => ({
    value: kb.kb_id,
    label: kb.name || kb.kb_id
  }))
)

const projectOptions = computed(() =>
  researchProjects.value.map((project) => ({
    value: project.project_id,
    label: project.title || project.project_id
  }))
)

const loadResearchProjects = async (kbId) => {
  if (!kbId) {
    researchProjects.value = []
    return
  }
  isLoadingProjects.value = true
  try {
    const result = await researchApi.listProjects(kbId, { status: 'active', limit: 100 })
    researchProjects.value = result.items || []
  } catch (error) {
    researchProjects.value = []
    console.error('加载研究项目失败:', error)
  } finally {
    isLoadingProjects.value = false
  }
}

const ensureResearchThread = async () => {
  if (!isResearchCopilotSelected.value || !researchKbId.value) return
  const generation = ++ensureGeneration
  ensuringThread.value = true
  try {
    const payload = {
      kb_id: researchKbId.value,
      project_id: researchProjectId.value || null,
      surface: 'projects'
    }
    const result = await researchApi.ensureCopilotThread(payload)
    if (generation !== ensureGeneration) return
    const thread = result?.thread
    if (!thread?.id) {
      throw new Error('研究助手会话响应无效')
    }
    chatThreadsStore.upsertThread(thread)
    await chatComponentRef.value?.selectThreadFromRoute?.(thread.id)
  } catch (error) {
    if (generation !== ensureGeneration) return
    message.error(error.message || '研究助手连接失败')
  } finally {
    if (generation === ensureGeneration) {
      ensuringThread.value = false
    }
  }
}

const handleResearchKbChange = (kbId) => {
  researchProjectId.value = undefined
  researchProjects.value = []
  if (kbId) {
    loadResearchProjects(kbId)
    ensureResearchThread()
  }
}

const handleResearchProjectChange = () => {
  ensureResearchThread()
}

// research-copilot 选中或知识库列表加载后，自动选择第一个知识库并确保会话
watch([isResearchCopilotSelected, knowledgeBaseOptions], () => {
  if (!isResearchCopilotSelected.value) return
  if (researchKbId.value) return
  const firstKb = knowledgeBaseOptions.value[0]
  if (firstKb) {
    researchKbId.value = firstKb.value
    handleResearchKbChange(firstKb.value)
  }
}, { immediate: true })

useOutsidePointerdown(agentDropdownOpen, [agentDropdownTriggerRef, agentDropdownPanelRef])
</script>

<style lang="less" scoped>
.agent-view {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100vh;
  overflow: hidden;
}

.agent-view-body {
  --gap-radius: 6px;
  display: flex;
  flex-direction: row;
  width: 100%;
  flex: 1;
  height: 100%;
  overflow: hidden;
  position: relative;

  .content {
    flex: 1;
    display: flex;
    flex-direction: column;
  }
}

.content {
  flex: 1;
  overflow: hidden;
}

.config-dropdown-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
  max-width: min(240px, calc(100vw - 160px));
  gap: 4px;
}

.config-dropdown-trigger :deep(svg) {
  color: currentColor;
}

.config-dropdown-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: currentColor;
}

.config-dropdown-chevron {
  flex-shrink: 0;
  color: currentColor;
}

.config-dropdown-compact-icon {
  display: none;
  flex-shrink: 0;
}

@container (max-width: 640px) {
  .config-dropdown-trigger {
    width: 30px;
    padding-inline: 0;
  }

  .config-dropdown-compact-icon {
    display: block;
  }

  .config-dropdown-text,
  .config-dropdown-chevron {
    display: none;
  }
}

// 响应式优化
@media (max-width: 520px) {
  .config-dropdown-trigger {
    max-width: calc(100vw - 112px);
  }
}

.research-context-bar {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  flex-shrink: 0;
}

.research-context-icon {
  flex-shrink: 0;
  color: var(--gray-500);
}

.research-context-spinner {
  flex-shrink: 0;
  color: var(--main-600);
  animation: research-context-spin 1s linear infinite;
}

.research-context-select {
  min-width: 100px;
  max-width: 160px;
}

.research-context-select :deep(.ant-select-selector) {
  min-height: 28px !important;
  padding: 0 6px !important;
  font-size: 12px;
  line-height: 26px;
}

.research-context-select :deep(.ant-select-selection-item) {
  font-size: 12px;
}

.research-context-select :deep(.ant-select-selection-placeholder) {
  font-size: 12px;
  color: var(--gray-500);
}

@keyframes research-context-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 640px) {
  .research-context-select {
    min-width: 80px;
    max-width: 120px;
  }
}

@media (max-width: 390px) {
  .research-context-project {
    display: none;
  }
}
</style>

<style lang="less">
.config-dropdown-overlay .config-dropdown-panel {
  min-width: 188px;
  max-width: min(260px, calc(100vw - 24px));
  padding: 4px;
  background: var(--gray-0);
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  box-shadow:
    0 8px 24px rgba(0, 0, 0, 0.08),
    0 2px 8px rgba(0, 0, 0, 0.04);
}

.config-dropdown-overlay .config-dropdown-item {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  width: 100%;
  padding: 6px 8px;
  border: none;
  border-radius: 6px;
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.config-dropdown-overlay .config-dropdown-item:hover {
  background: var(--gray-50);
}

.config-dropdown-overlay .config-dropdown-item.disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.config-dropdown-overlay .config-dropdown-item.selected {
  background: var(--gray-50);
}

.config-dropdown-overlay .config-dropdown-item.action-item {
  color: var(--gray-800);
}

.config-dropdown-overlay .config-dropdown-item-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  line-height: 1.35;
  color: var(--gray-800);
}

.config-dropdown-overlay .config-dropdown-item-icon,
.config-dropdown-overlay .config-dropdown-item-icon-image,
.config-dropdown-overlay .config-dropdown-item-icon-empty {
  flex-shrink: 0;
}

.config-dropdown-overlay .config-dropdown-item-icon {
  color: var(--gray-500);
}

.config-dropdown-overlay .config-dropdown-item-icon-image,
.config-dropdown-overlay .config-dropdown-item-icon-empty {
  width: 24px;
  height: 24px;
  border-radius: 4px;
}

.config-dropdown-overlay .config-dropdown-item-icon-image {
  object-fit: cover;
}

.config-dropdown-overlay .config-dropdown-item-badge {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 11px;
  line-height: 1.4;
}

.config-dropdown-overlay .config-dropdown-item-check {
  flex-shrink: 0;
  color: var(--main-600);
}

.config-dropdown-overlay .config-dropdown-hint {
  padding: 6px 8px;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 1.4;
}

.config-dropdown-overlay .config-dropdown-divider {
  height: 1px;
  margin: 4px 4px;
  background: var(--gray-100);
}
</style>
