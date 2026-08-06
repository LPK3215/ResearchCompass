<template>
  <section class="execution-plan">
    <header class="plan-header">
      <div>
        <h3>执行计划</h3>
        <span>{{ plan?.summary?.tasks?.open || 0 }} 项待完成</span>
      </div>
      <div v-if="!readOnly" class="plan-actions">
        <a-button data-testid="create-research-task" class="lucide-icon-btn" @click="openTaskModal(null, null)">
          <template #icon><ListPlus :size="15" /></template>
          新建任务
        </a-button>
        <a-button data-testid="create-research-milestone" type="primary" class="lucide-icon-btn" @click="openMilestoneModal(null)">
          <template #icon><Flag :size="15" /></template>
          新建里程碑
        </a-button>
      </div>
    </header>

    <a-alert v-if="error" type="error" show-icon :message="error" closable @close="error = ''" />
    <a-skeleton v-if="loading && !plan" active :paragraph="{ rows: 9 }" />

    <template v-else-if="plan">
      <div class="plan-metrics">
        <div>
          <span>执行进度</span>
          <strong>{{ plan.summary.progress }}%</strong>
        </div>
        <div>
          <span>进行中</span>
          <strong>{{ plan.summary.tasks.in_progress }}</strong>
        </div>
        <div :class="{ attention: plan.summary.tasks.blocked }">
          <span>受阻</span>
          <strong>{{ plan.summary.tasks.blocked }}</strong>
        </div>
        <div :class="{ danger: plan.summary.tasks.overdue }">
          <span>已逾期</span>
          <strong>{{ plan.summary.tasks.overdue + plan.summary.milestones.overdue }}</strong>
        </div>
        <div>
          <span>下一截止</span>
          <strong class="metric-date">{{ formatDate(plan.summary.next_due_date) || '未设置' }}</strong>
        </div>
      </div>

      <div v-if="plan.milestones.length" class="milestone-list">
        <section
          v-for="(milestone, milestoneIndex) in plan.milestones"
          :key="milestone.milestone_id"
          class="milestone-section"
        >
          <header class="milestone-header">
            <div class="milestone-order">{{ milestoneIndex + 1 }}</div>
            <div class="milestone-main">
              <div class="milestone-title-line">
                <strong>{{ milestone.title }}</strong>
                <span class="state-label" :class="`state-${milestone.status}`">
                  {{ milestoneStatusLabel(milestone.status) }}
                </span>
              </div>
              <p v-if="milestone.description">{{ milestone.description }}</p>
              <div class="milestone-meta">
                <span><CalendarDays :size="13" />{{ formatDate(milestone.target_date) || '未设置日期' }}</span>
                <span>{{ milestone.task_counts.completed }}/{{ milestone.task_counts.total }} 项完成</span>
              </div>
            </div>
            <div class="milestone-progress">
              <a-progress type="circle" :percent="milestone.progress" :size="44" :stroke-width="8" />
            </div>
            <div v-if="!readOnly" class="row-actions">
              <a-tooltip title="上移">
                <a-button
                  type="text"
                  class="icon-action"
                  :disabled="milestoneIndex === 0 || ordering"
                  aria-label="上移里程碑"
                  @click="moveMilestone(milestoneIndex, -1)"
                ><ArrowUp :size="15" /></a-button>
              </a-tooltip>
              <a-tooltip title="下移">
                <a-button
                  type="text"
                  class="icon-action"
                  :disabled="milestoneIndex === plan.milestones.length - 1 || ordering"
                  aria-label="下移里程碑"
                  @click="moveMilestone(milestoneIndex, 1)"
                ><ArrowDown :size="15" /></a-button>
              </a-tooltip>
              <a-tooltip title="编辑里程碑">
                <a-button type="text" class="icon-action" aria-label="编辑里程碑" @click="openMilestoneModal(milestone)">
                  <Pencil :size="15" />
                </a-button>
              </a-tooltip>
              <a-tooltip title="关联研究成果">
                <a-button type="text" class="icon-action" aria-label="关联里程碑成果" @click="openLinkModal('milestone', milestone)">
                  <Link2 :size="15" />
                </a-button>
              </a-tooltip>
              <a-popconfirm
                title="删除这个里程碑？"
                ok-text="删除"
                cancel-text="取消"
                @confirm="removeMilestone(milestone)"
              >
                <a-button type="text" danger class="icon-action" aria-label="删除里程碑"><Trash2 :size="15" /></a-button>
              </a-popconfirm>
            </div>
          </header>

          <div v-if="milestone.asset_links.length" class="linked-assets milestone-links">
            <span
              v-for="link in milestone.asset_links"
              :key="link.link_id"
              class="asset-link"
              :class="{ unavailable: !link.asset.available }"
            >
              <Paperclip :size="12" />{{ link.asset.title }}
              <a-button
                v-if="!readOnly"
                type="text"
                aria-label="解除成果关联"
                @click="removeAssetLink(link)"
              ><X :size="12" /></a-button>
            </span>
          </div>

          <div v-if="milestone.tasks.length" class="task-list">
            <article v-for="(task, taskIndex) in milestone.tasks" :key="task.task_id" class="task-row">
              <a-select
                data-testid="research-task-status"
                :value="task.status"
                :options="taskStatusOptions"
                :disabled="readOnly || taskUpdatingId === task.task_id"
                class="task-status-select"
                @change="updateTaskStatus(task, $event)"
              />
              <div class="task-content">
                <div class="task-title-line">
                  <strong :class="{ completed: task.status === 'done' }">{{ task.title }}</strong>
                  <span class="priority-label" :class="`priority-${task.priority}`">{{ priorityLabel(task.priority) }}</span>
                </div>
                <p v-if="task.description">{{ task.description }}</p>
                <div class="task-meta">
                  <span :class="{ overdue: isOverdue(task) }">
                    <CalendarDays :size="13" />{{ formatDate(task.due_date) || '未设置截止日期' }}
                  </span>
                  <span v-if="task.asset_links.length"><Paperclip :size="13" />{{ task.asset_links.length }} 项证据</span>
                </div>
                <div v-if="task.asset_links.length" class="linked-assets">
                  <span
                    v-for="link in task.asset_links"
                    :key="link.link_id"
                    class="asset-link"
                    :class="{ unavailable: !link.asset.available }"
                  >
                    {{ link.asset.title }}
                    <a-button
                      v-if="!readOnly"
                      type="text"
                      aria-label="解除成果关联"
                      @click="removeAssetLink(link)"
                    ><X :size="12" /></a-button>
                  </span>
                </div>
              </div>
              <div v-if="!readOnly" class="row-actions task-actions">
                <a-tooltip title="上移">
                  <a-button
                    type="text"
                    class="icon-action"
                    :disabled="taskIndex === 0 || ordering"
                    aria-label="上移任务"
                    @click="moveTask(milestone.milestone_id, milestone.tasks, taskIndex, -1)"
                  ><ArrowUp :size="14" /></a-button>
                </a-tooltip>
                <a-tooltip title="下移">
                  <a-button
                    type="text"
                    class="icon-action"
                    :disabled="taskIndex === milestone.tasks.length - 1 || ordering"
                    aria-label="下移任务"
                    @click="moveTask(milestone.milestone_id, milestone.tasks, taskIndex, 1)"
                  ><ArrowDown :size="14" /></a-button>
                </a-tooltip>
                <a-tooltip title="编辑任务">
                  <a-button type="text" class="icon-action" aria-label="编辑任务" @click="openTaskModal(task, milestone.milestone_id)">
                    <Pencil :size="14" />
                  </a-button>
                </a-tooltip>
                <a-tooltip title="关联研究成果">
                  <a-button type="text" class="icon-action" aria-label="关联任务成果" @click="openLinkModal('task', task)">
                    <Link2 :size="14" />
                  </a-button>
                </a-tooltip>
                <a-popconfirm title="删除这个任务？" ok-text="删除" cancel-text="取消" @confirm="removeTask(task)">
                  <a-button type="text" danger class="icon-action" aria-label="删除任务"><Trash2 :size="14" /></a-button>
                </a-popconfirm>
              </div>
            </article>
          </div>
          <div v-else class="empty-group">暂无任务</div>
          <a-button v-if="!readOnly" type="dashed" block class="add-task" @click="openTaskModal(null, milestone.milestone_id)">
            <Plus :size="14" />添加任务
          </a-button>
        </section>
      </div>

      <section v-if="plan.unassigned_tasks.length || !readOnly" class="milestone-section unassigned-section">
        <header class="milestone-header compact">
          <div class="milestone-order"><Inbox :size="16" /></div>
          <div class="milestone-main">
            <div class="milestone-title-line"><strong>未分配任务</strong></div>
            <div class="milestone-meta"><span>{{ plan.unassigned_tasks.length }} 项</span></div>
          </div>
        </header>
        <div v-if="plan.unassigned_tasks.length" class="task-list">
          <article v-for="(task, taskIndex) in plan.unassigned_tasks" :key="task.task_id" class="task-row">
            <a-select
              data-testid="research-task-status"
              :value="task.status"
              :options="taskStatusOptions"
              :disabled="readOnly || taskUpdatingId === task.task_id"
              class="task-status-select"
              @change="updateTaskStatus(task, $event)"
            />
            <div class="task-content">
              <div class="task-title-line">
                <strong :class="{ completed: task.status === 'done' }">{{ task.title }}</strong>
                <span class="priority-label" :class="`priority-${task.priority}`">{{ priorityLabel(task.priority) }}</span>
              </div>
              <p v-if="task.description">{{ task.description }}</p>
              <div class="task-meta">
                <span :class="{ overdue: isOverdue(task) }"><CalendarDays :size="13" />{{ formatDate(task.due_date) || '未设置截止日期' }}</span>
              </div>
              <div v-if="task.asset_links.length" class="linked-assets">
                <span v-for="link in task.asset_links" :key="link.link_id" class="asset-link">
                  {{ link.asset.title }}
                  <a-button v-if="!readOnly" type="text" aria-label="解除成果关联" @click="removeAssetLink(link)"><X :size="12" /></a-button>
                </span>
              </div>
            </div>
            <div v-if="!readOnly" class="row-actions task-actions">
              <a-button type="text" class="icon-action" :disabled="taskIndex === 0 || ordering" aria-label="上移任务" @click="moveTask(null, plan.unassigned_tasks, taskIndex, -1)"><ArrowUp :size="14" /></a-button>
              <a-button type="text" class="icon-action" :disabled="taskIndex === plan.unassigned_tasks.length - 1 || ordering" aria-label="下移任务" @click="moveTask(null, plan.unassigned_tasks, taskIndex, 1)"><ArrowDown :size="14" /></a-button>
              <a-button type="text" class="icon-action" aria-label="编辑任务" @click="openTaskModal(task, null)"><Pencil :size="14" /></a-button>
              <a-button type="text" class="icon-action" aria-label="关联任务成果" @click="openLinkModal('task', task)"><Link2 :size="14" /></a-button>
              <a-popconfirm title="删除这个任务？" ok-text="删除" cancel-text="取消" @confirm="removeTask(task)">
                <a-button type="text" danger class="icon-action" aria-label="删除任务"><Trash2 :size="14" /></a-button>
              </a-popconfirm>
            </div>
          </article>
        </div>
        <a-button v-if="!readOnly" type="dashed" block class="add-task" @click="openTaskModal(null, null)">
          <Plus :size="14" />添加未分配任务
        </a-button>
      </section>

      <div v-if="!plan.milestones.length && !plan.unassigned_tasks.length" class="plan-empty">
        <ListChecks :size="34" />
        <strong>尚未创建执行计划</strong>
      </div>
    </template>

    <a-modal
      v-model:open="milestoneModalOpen"
      :title="editingMilestone ? '编辑里程碑' : '新建里程碑'"
      :confirm-loading="saving"
      ok-text="保存"
      cancel-text="取消"
      @ok="saveMilestone"
    >
      <a-form layout="vertical">
        <a-form-item label="里程碑名称" required>
          <a-input data-testid="research-milestone-title" v-model:value="milestoneForm.title" :maxlength="255" show-count />
        </a-form-item>
        <a-form-item label="说明">
          <a-textarea v-model:value="milestoneForm.description" :rows="3" :maxlength="12000" show-count />
        </a-form-item>
        <div class="form-grid">
          <a-form-item label="状态">
            <a-select v-model:value="milestoneForm.status" :options="milestoneStatusOptions" />
          </a-form-item>
          <a-form-item label="目标日期">
            <a-input v-model:value="milestoneForm.targetDate" type="date" />
          </a-form-item>
        </div>
      </a-form>
    </a-modal>

    <a-modal
      v-model:open="taskModalOpen"
      :title="editingTask ? '编辑任务' : '新建任务'"
      width="640px"
      :confirm-loading="saving"
      ok-text="保存"
      cancel-text="取消"
      @ok="saveTask"
    >
      <a-form layout="vertical">
        <a-form-item label="任务名称" required>
          <a-input data-testid="research-task-title" v-model:value="taskForm.title" :maxlength="255" show-count />
        </a-form-item>
        <a-form-item label="说明">
          <a-textarea v-model:value="taskForm.description" :rows="3" :maxlength="12000" show-count />
        </a-form-item>
        <div class="form-grid task-form-grid">
          <a-form-item label="状态"><a-select v-model:value="taskForm.status" :options="taskStatusOptions" /></a-form-item>
          <a-form-item label="优先级"><a-select v-model:value="taskForm.priority" :options="priorityOptions" /></a-form-item>
          <a-form-item label="截止日期"><a-input v-model:value="taskForm.dueDate" type="date" /></a-form-item>
        </div>
        <a-form-item label="所属里程碑">
          <a-select v-model:value="taskForm.milestoneId" allow-clear :options="milestoneOptions" placeholder="未分配" />
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal
      v-model:open="linkModalOpen"
      title="关联研究成果"
      :confirm-loading="linkSaving"
      ok-text="关联"
      cancel-text="取消"
      @ok="saveAssetLinks"
    >
      <a-form layout="vertical">
        <a-form-item label="执行项"><a-input :value="linkTarget?.title" disabled /></a-form-item>
        <a-form-item label="研究成果" required>
          <a-select
            v-model:value="selectedAssetIds"
            mode="multiple"
            show-search
            option-filter-prop="label"
            :loading="assetsLoading"
            :options="linkableAssetOptions"
            placeholder="选择一项或多项成果"
          />
        </a-form-item>
      </a-form>
    </a-modal>
  </section>
</template>

<script setup>
// ResearchCompass 研究项目执行计划
// 本组件是本仓库在开源智能体框架 Yuxi 之上实现的科研业务界面，负责单个研究项目的
// 里程碑、任务、未分配任务与成果证据关联的增删改查与排序，并展示进度、受阻、逾期
// 等计划健康指标。通用表单、模态框与请求基础设施由 Yuxi 提供。
import { computed, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import {
  ArrowDown,
  ArrowUp,
  CalendarDays,
  Flag,
  Inbox,
  Link2,
  ListChecks,
  ListPlus,
  Paperclip,
  Pencil,
  Plus,
  Trash2,
  X
} from 'lucide-vue-next'
import { researchApi } from '@/apis/research_api'

const props = defineProps({
  project: { type: Object, required: true }
})
const emit = defineEmits(['changed'])

const plan = ref(null)
const loading = ref(false)
const error = ref('')
const saving = ref(false)
const ordering = ref(false)
const taskUpdatingId = ref('')
const milestoneModalOpen = ref(false)
const taskModalOpen = ref(false)
const linkModalOpen = ref(false)
const linkSaving = ref(false)
const assetsLoading = ref(false)
const projectAssets = ref([])
const editingMilestone = ref(null)
const editingTask = ref(null)
const linkTargetType = ref('')
const linkTarget = ref(null)
const selectedAssetIds = ref([])
let requestSequence = 0

const milestoneForm = reactive({ title: '', description: '', status: 'planned', targetDate: '' })
const taskForm = reactive({
  title: '', description: '', status: 'todo', priority: 'medium', dueDate: '', milestoneId: undefined
})

const milestoneStatusOptions = [
  { value: 'planned', label: '待开始' },
  { value: 'active', label: '进行中' },
  { value: 'completed', label: '已完成' }
]
const taskStatusOptions = [
  { value: 'todo', label: '待处理' },
  { value: 'in_progress', label: '进行中' },
  { value: 'blocked', label: '受阻' },
  { value: 'done', label: '已完成' }
]
const priorityOptions = [
  { value: 'low', label: '低' },
  { value: 'medium', label: '中' },
  { value: 'high', label: '高' }
]

const readOnly = computed(() => props.project.status !== 'active')
const milestoneOptions = computed(() => (plan.value?.milestones || []).map((item) => ({
  value: item.milestone_id, label: item.title
})))
const linkableAssetOptions = computed(() => {
  const linkedIds = new Set((linkTarget.value?.asset_links || []).map((link) => link.asset_id))
  return projectAssets.value
    .filter((asset) => !linkedIds.has(asset.asset_id))
    .map((asset) => ({
      value: asset.asset_id,
      label: `${asset.title}${asset.available ? '' : '（源成果不可用）'}`,
      disabled: !asset.available
    }))
})

const milestoneStatusLabel = (status) => milestoneStatusOptions.find((item) => item.value === status)?.label || status
const priorityLabel = (priority) => priorityOptions.find((item) => item.value === priority)?.label || priority
const formatDate = (value) => {
  if (!value) return ''
  const parsed = new Date(`${value}T00:00:00`)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString('zh-CN')
}
const isOverdue = (task) => task.status !== 'done' && task.due_date && task.due_date < new Date().toISOString().slice(0, 10)

const loadPlan = async () => {
  const sequence = ++requestSequence
  loading.value = true
  error.value = ''
  try {
    const result = await researchApi.getProjectPlan(props.project.project_id)
    if (sequence === requestSequence) plan.value = result
  } catch (loadError) {
    if (sequence === requestSequence) error.value = loadError.message || '执行计划加载失败'
  } finally {
    if (sequence === requestSequence) loading.value = false
  }
}

const notifyChanged = async () => {
  await loadPlan()
  emit('changed')
}

const openMilestoneModal = (milestone) => {
  editingMilestone.value = milestone
  Object.assign(milestoneForm, {
    title: milestone?.title || '',
    description: milestone?.description || '',
    status: milestone?.status || 'planned',
    targetDate: milestone?.target_date || ''
  })
  milestoneModalOpen.value = true
}

const saveMilestone = async () => {
  if (!milestoneForm.title.trim()) {
    message.warning('请输入里程碑名称')
    return
  }
  saving.value = true
  try {
    const payload = {
      title: milestoneForm.title.trim(),
      description: milestoneForm.description.trim(),
      status: milestoneForm.status,
      target_date: milestoneForm.targetDate || null
    }
    if (editingMilestone.value) {
      await researchApi.updateProjectMilestone(
        props.project.project_id, editingMilestone.value.milestone_id, payload
      )
    } else {
      await researchApi.createProjectMilestone(props.project.project_id, payload)
    }
    milestoneModalOpen.value = false
    message.success(editingMilestone.value ? '里程碑已更新' : '里程碑已创建')
    await notifyChanged()
  } catch (saveError) {
    message.error(saveError.message || '里程碑保存失败')
  } finally {
    saving.value = false
  }
}

const removeMilestone = async (milestone) => {
  try {
    await researchApi.deleteProjectMilestone(props.project.project_id, milestone.milestone_id)
    message.success('里程碑已删除')
    await notifyChanged()
  } catch (removeError) {
    message.error(removeError.message || '里程碑删除失败')
  }
}

const moveMilestone = async (index, offset) => {
  const reordered = [...plan.value.milestones]
  ;[reordered[index], reordered[index + offset]] = [reordered[index + offset], reordered[index]]
  ordering.value = true
  try {
    await researchApi.reorderProjectMilestones(
      props.project.project_id, reordered.map((item) => item.milestone_id)
    )
    await notifyChanged()
  } catch (moveError) {
    message.error(moveError.message || '里程碑排序失败')
  } finally {
    ordering.value = false
  }
}

const openTaskModal = (task, milestoneId) => {
  editingTask.value = task
  Object.assign(taskForm, {
    title: task?.title || '',
    description: task?.description || '',
    status: task?.status || 'todo',
    priority: task?.priority || 'medium',
    dueDate: task?.due_date || '',
    milestoneId: task ? (task.milestone_id || undefined) : (milestoneId || undefined)
  })
  taskModalOpen.value = true
}

const saveTask = async () => {
  if (!taskForm.title.trim()) {
    message.warning('请输入任务名称')
    return
  }
  saving.value = true
  try {
    const payload = {
      title: taskForm.title.trim(),
      description: taskForm.description.trim(),
      status: taskForm.status,
      priority: taskForm.priority,
      due_date: taskForm.dueDate || null,
      milestone_id: taskForm.milestoneId || null
    }
    if (editingTask.value) {
      await researchApi.updateProjectTask(props.project.project_id, editingTask.value.task_id, payload)
    } else {
      await researchApi.createProjectTask(props.project.project_id, payload)
    }
    taskModalOpen.value = false
    message.success(editingTask.value ? '任务已更新' : '任务已创建')
    await notifyChanged()
  } catch (saveError) {
    message.error(saveError.message || '任务保存失败')
  } finally {
    saving.value = false
  }
}

const updateTaskStatus = async (task, status) => {
  taskUpdatingId.value = task.task_id
  try {
    await researchApi.updateProjectTask(props.project.project_id, task.task_id, { status })
    await notifyChanged()
  } catch (updateError) {
    message.error(updateError.message || '任务状态更新失败')
  } finally {
    taskUpdatingId.value = ''
  }
}

const removeTask = async (task) => {
  try {
    await researchApi.deleteProjectTask(props.project.project_id, task.task_id)
    message.success('任务已删除')
    await notifyChanged()
  } catch (removeError) {
    message.error(removeError.message || '任务删除失败')
  }
}

const moveTask = async (milestoneId, tasks, index, offset) => {
  const reordered = [...tasks]
  ;[reordered[index], reordered[index + offset]] = [reordered[index + offset], reordered[index]]
  ordering.value = true
  try {
    await researchApi.reorderProjectTasks(props.project.project_id, {
      milestone_id: milestoneId,
      task_ids: reordered.map((item) => item.task_id)
    })
    await notifyChanged()
  } catch (moveError) {
    message.error(moveError.message || '任务排序失败')
  } finally {
    ordering.value = false
  }
}

const loadProjectAssets = async () => {
  assetsLoading.value = true
  try {
    const items = []
    let offset = 0
    let hasMore = true
    while (hasMore) {
      const result = await researchApi.listProjectAssets(props.project.project_id, { offset, limit: 100 })
      items.push(...(result.items || []))
      offset += result.items?.length || 0
      hasMore = Boolean(result.has_more)
    }
    projectAssets.value = items
  } catch (loadError) {
    message.error(loadError.message || '研究成果加载失败')
  } finally {
    assetsLoading.value = false
  }
}

const openLinkModal = async (targetType, target) => {
  linkTargetType.value = targetType
  linkTarget.value = target
  selectedAssetIds.value = []
  linkModalOpen.value = true
  await loadProjectAssets()
}

const saveAssetLinks = async () => {
  if (!selectedAssetIds.value.length) {
    message.warning('请选择研究成果')
    return
  }
  linkSaving.value = true
  try {
    await Promise.all(selectedAssetIds.value.map((assetId) => researchApi.createProjectPlanAssetLink(
      props.project.project_id,
      {
        asset_id: assetId,
        milestone_id: linkTargetType.value === 'milestone' ? linkTarget.value.milestone_id : null,
        task_id: linkTargetType.value === 'task' ? linkTarget.value.task_id : null
      }
    )))
    linkModalOpen.value = false
    message.success('研究成果已关联')
    await notifyChanged()
  } catch (linkError) {
    message.error(linkError.message || '研究成果关联失败')
  } finally {
    linkSaving.value = false
  }
}

const removeAssetLink = async (link) => {
  try {
    await researchApi.deleteProjectPlanAssetLink(props.project.project_id, link.link_id)
    message.success('成果关联已解除')
    await notifyChanged()
  } catch (removeError) {
    message.error(removeError.message || '成果关联解除失败')
  }
}

defineExpose({ refresh: loadPlan })

watch(() => props.project.project_id, () => {
  plan.value = null
  void loadPlan()
}, { immediate: true })
</script>

<style scoped lang="less">
.execution-plan { min-width: 0; }
.plan-header,
.plan-actions,
.milestone-header,
.milestone-title-line,
.milestone-meta,
.task-title-line,
.task-meta,
.linked-assets,
.row-actions { display: flex; align-items: center; }
.plan-header { justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.plan-header h3 { margin: 0; color: var(--color-text); font-size: 16px; }
.plan-header span { color: var(--color-text-tertiary); font-size: 12px; }
.plan-actions { flex-wrap: wrap; gap: 8px; }
.plan-metrics {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  margin-bottom: 18px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  background: var(--gray-25);
}
.plan-metrics > div { min-width: 0; padding: 12px 14px; border-right: 1px solid var(--gray-150); }
.plan-metrics > div:last-child { border-right: 0; }
.plan-metrics span { display: block; color: var(--color-text-tertiary); font-size: 11px; }
.plan-metrics strong { display: block; margin-top: 2px; color: var(--color-text); font-size: 20px; }
.plan-metrics .metric-date { overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.plan-metrics .attention strong { color: var(--color-warning-700); }
.plan-metrics .danger strong { color: var(--color-error-700); }
.milestone-list { border-top: 1px solid var(--gray-150); }
.milestone-section { padding: 18px 0; border-bottom: 1px solid var(--gray-150); }
.milestone-header { align-items: flex-start; gap: 12px; }
.milestone-order {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--main-50);
  color: var(--main-700);
  font-size: 12px;
  font-weight: 600;
}
.milestone-main { flex: 1; min-width: 0; }
.milestone-title-line { flex-wrap: wrap; gap: 8px; }
.milestone-title-line strong { overflow-wrap: anywhere; color: var(--color-text); font-size: 15px; }
.milestone-main > p { margin: 6px 0; color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; white-space: pre-wrap; }
.milestone-meta { flex-wrap: wrap; gap: 12px; color: var(--color-text-tertiary); font-size: 11px; }
.milestone-meta span,
.task-meta span { display: inline-flex; align-items: center; gap: 4px; }
.milestone-progress { flex: none; }
.row-actions { flex: none; gap: 1px; }
.icon-action { display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; padding: 0; }
.state-label,
.priority-label { flex: none; padding: 2px 7px; border-radius: 999px; font-size: 10px; font-weight: 500; }
.state-planned { background: var(--gray-100); color: var(--color-text-secondary); }
.state-active { background: var(--main-50); color: var(--main-700); }
.state-completed { background: var(--color-success-50); color: var(--color-success-700); }
.task-list { margin-top: 14px; border-top: 1px solid var(--gray-100); }
.task-row {
  display: grid;
  grid-template-columns: 106px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: start;
  padding: 13px 0;
  border-bottom: 1px solid var(--gray-100);
}
.task-status-select { width: 106px; }
.task-content { min-width: 0; }
.task-title-line { flex-wrap: wrap; gap: 7px; }
.task-title-line strong { overflow-wrap: anywhere; color: var(--color-text); font-size: 13px; }
.task-title-line strong.completed { color: var(--color-text-tertiary); text-decoration: line-through; }
.task-content > p { margin: 5px 0; color: var(--color-text-secondary); font-size: 12px; line-height: 1.45; white-space: pre-wrap; }
.task-meta { flex-wrap: wrap; gap: 12px; color: var(--color-text-tertiary); font-size: 11px; }
.task-meta .overdue { color: var(--color-error-700); }
.priority-low { background: var(--gray-100); color: var(--color-text-secondary); }
.priority-medium { background: var(--color-warning-50); color: var(--color-warning-700); }
.priority-high { background: var(--color-error-50); color: var(--color-error-700); }
.linked-assets { flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.milestone-links { padding-left: 42px; }
.asset-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  padding: 3px 7px;
  border: 1px solid var(--gray-150);
  border-radius: 4px;
  background: var(--gray-25);
  color: var(--color-text-secondary);
  font-size: 11px;
  overflow-wrap: anywhere;
}
.asset-link.unavailable { border-style: dashed; color: var(--color-text-tertiary); }
.asset-link :deep(.ant-btn) { width: 18px; height: 18px; min-width: 18px; padding: 0; }
.add-task { display: flex; gap: 5px; align-items: center; justify-content: center; margin-top: 10px; }
.empty-group { padding: 15px 0 4px 42px; color: var(--color-text-tertiary); font-size: 12px; }
.unassigned-section { margin-top: 8px; border-top: 1px solid var(--gray-150); }
.milestone-header.compact { align-items: center; }
.plan-empty { display: flex; flex-direction: column; gap: 9px; align-items: center; padding: 56px 20px; color: var(--color-text-tertiary); }
.plan-empty strong { color: var(--color-text-secondary); font-size: 13px; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.task-form-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }

@media (max-width: 900px) {
  .plan-metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .plan-metrics > div:nth-child(3) { border-right: 0; }
  .plan-metrics > div:nth-child(-n + 3) { border-bottom: 1px solid var(--gray-150); }
  .task-row { grid-template-columns: 106px minmax(0, 1fr); }
  .task-actions { grid-column: 2; justify-content: flex-start; }
}

@media (max-width: 640px) {
  .plan-header { align-items: stretch; flex-direction: column; }
  .plan-actions { display: grid; grid-template-columns: 1fr 1fr; }
  .plan-actions :deep(.ant-btn) { width: 100%; }
  .plan-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .plan-metrics > div { border-right: 1px solid var(--gray-150) !important; border-bottom: 1px solid var(--gray-150); }
  .plan-metrics > div:nth-child(2n) { border-right: 0 !important; }
  .plan-metrics > div:last-child { grid-column: 1 / -1; border-right: 0 !important; border-bottom: 0; }
  .milestone-header { display: grid; grid-template-columns: 30px minmax(0, 1fr); }
  .milestone-progress { grid-column: 1; }
  .milestone-header > .row-actions { grid-column: 2; justify-content: flex-start; }
  .milestone-links { padding-left: 0; }
  .task-row { grid-template-columns: 1fr; }
  .task-status-select { width: 100%; }
  .task-actions { grid-column: 1; }
  .empty-group { padding-left: 0; }
  .form-grid,
  .task-form-grid { grid-template-columns: 1fr; gap: 0; }
}
</style>
