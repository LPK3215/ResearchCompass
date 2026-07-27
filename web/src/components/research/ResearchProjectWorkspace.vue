<template>
  <section class="project-workspace">
    <div class="project-toolbar">
      <div class="toolbar-field database-field">
        <label for="project-database">论文知识库</label>
        <a-select
          id="project-database"
          :value="kbId"
          :options="databaseOptions"
          :loading="databasesLoading"
          show-search
          option-filter-prop="label"
          @change="$emit('change-database', $event)"
        />
      </div>
      <a-button class="lucide-icon-btn" :loading="projectsLoading" :disabled="!kbId" @click="loadProjects">
        <template #icon><RefreshCw :size="15" /></template>
        刷新
      </a-button>
    </div>

    <a-alert v-if="workspaceError" type="error" show-icon :message="workspaceError" closable @close="workspaceError = ''" />

    <div v-if="kbId" class="project-layout">
      <aside class="project-sidebar">
        <header class="sidebar-header">
          <div>
            <h2>研究项目</h2>
            <span>{{ projectsTotal }} 个项目</span>
          </div>
          <a-tooltip title="新建研究项目">
            <a-button type="primary" class="square-action" aria-label="新建研究项目" @click="openCreateProject">
              <Plus :size="17" />
            </a-button>
          </a-tooltip>
        </header>

        <div class="project-filters">
          <a-input-search
            v-model:value="projectQuery"
            allow-clear
            :maxlength="500"
            placeholder="搜索项目"
            @search="applyProjectFilters"
          />
          <a-segmented v-model:value="projectStatus" block :options="statusFilterOptions" @change="applyProjectFilters" />
        </div>

        <a-skeleton v-if="projectsLoading && !projects.length" active :paragraph="{ rows: 6 }" class="sidebar-loading" />
        <div v-else-if="projects.length" class="project-list">
          <button
            v-for="project in projects"
            :key="project.project_id"
            type="button"
            class="project-list-item"
            :class="{ active: project.project_id === selectedProjectId }"
            @click="selectProject(project.project_id)"
          >
            <span class="project-item-heading">
              <strong>{{ project.title }}</strong>
              <span class="project-status" :class="`status-${project.status}`">{{ statusLabel(project.status) }}</span>
            </span>
            <span class="project-item-question">{{ project.research_question }}</span>
            <span class="project-item-progress">
              <span><span :style="{ width: `${project.progress}%` }" /></span>
              <em>{{ project.progress }}%</em>
            </span>
            <span class="project-item-meta">
              <span class="health-label" :class="`health-${project.health}`">{{ healthLabel(project.health) }}</span>
              <span><ListTodo :size="13" />{{ project.plan_summary?.tasks?.open || 0 }}</span>
              <time>{{ formatCompactDate(project.updated_at) }}</time>
            </span>
          </button>
        </div>
        <div v-else class="project-list-empty">
          <FolderKanban :size="30" />
          <strong>暂无研究项目</strong>
          <a-button type="primary" size="small" @click="openCreateProject">新建项目</a-button>
        </div>

        <a-pagination
          v-if="projectsTotal > projectPageSize"
          v-model:current="projectPage"
          :page-size="projectPageSize"
          :total="projectsTotal"
          :show-size-changer="false"
          size="small"
          class="sidebar-pagination"
          @change="loadProjects"
        />
      </aside>

      <main class="project-detail">
        <a-skeleton v-if="detailLoading && !projectDetail" active :paragraph="{ rows: 12 }" class="detail-skeleton" />
        <div v-else-if="projectDetail" class="detail-content">
          <header class="project-detail-header">
            <div class="project-title-block">
              <div class="project-title-line">
                <span class="project-status" :class="`status-${projectDetail.status}`">{{ statusLabel(projectDetail.status) }}</span>
                <h2>{{ projectDetail.title }}</h2>
              </div>
              <p>{{ projectDetail.research_question }}</p>
              <div class="project-meta">
                <span v-if="projectDetail.target_date"><CalendarDays :size="14" />目标 {{ formatDate(projectDetail.target_date) }}</span>
                <span><Clock3 :size="14" />更新 {{ formatDateTime(projectDetail.updated_at) }}</span>
                <a-tag v-for="tag in projectDetail.tags" :key="tag">{{ tag }}</a-tag>
              </div>
            </div>
            <div class="project-actions">
              <a-button
                v-if="projectDetail.status !== 'archived'"
                class="lucide-icon-btn"
                @click="openEditProject"
              >
                <template #icon><Pencil :size="15" /></template>
                编辑
              </a-button>
              <a-button
                v-if="projectDetail.status === 'active'"
                type="primary"
                class="lucide-icon-btn"
                :loading="statusUpdating === 'completed'"
                @click="changeProjectStatus('completed')"
              >
                <template #icon><CircleCheck :size="15" /></template>
                标记完成
              </a-button>
              <a-button
                v-else
                class="lucide-icon-btn"
                :loading="statusUpdating === 'active'"
                @click="changeProjectStatus('active')"
              >
                <template #icon><RotateCcw :size="15" /></template>
                恢复进行中
              </a-button>
              <a-popconfirm
                v-if="projectDetail.status !== 'archived'"
                title="归档后项目将变为只读，确认归档？"
                ok-text="归档"
                cancel-text="取消"
                @confirm="changeProjectStatus('archived')"
              >
                <a-button class="lucide-icon-btn" :loading="statusUpdating === 'archived'">
                  <template #icon><Archive :size="15" /></template>
                  归档
                </a-button>
              </a-popconfirm>
              <a-dropdown :trigger="['click']">
                <a-button class="lucide-icon-btn" :loading="reportDownloading">
                  <template #icon><Download :size="15" /></template>
                  导出报告
                  <ChevronDown :size="14" />
                </a-button>
                <template #overlay>
                  <a-menu @click="downloadReport">
                    <a-menu-item key="markdown">Markdown 报告</a-menu-item>
                    <a-menu-item key="docx">Word 报告</a-menu-item>
                  </a-menu>
                </template>
              </a-dropdown>
              <a-popconfirm
                title="删除项目？论文和研究运行不会被删除。"
                ok-text="删除项目"
                cancel-text="取消"
                @confirm="deleteCurrentProject"
              >
                <a-button danger class="icon-only-action" :loading="projectDeleting" aria-label="删除项目">
                  <Trash2 :size="16" />
                </a-button>
              </a-popconfirm>
            </div>
          </header>

          <section class="project-overview">
            <div class="progress-block">
              <div class="overview-heading">
                <span>项目进度 · {{ projectDetail.progress_source === 'tasks' ? '任务自动计算' : '手动维护' }}</span>
                <strong>{{ projectDetail.progress }}%</strong>
              </div>
              <a-progress :percent="projectDetail.progress" :show-info="false" stroke-color="var(--main-color)" />
              <span class="health-label overview-health" :class="`health-${projectDetail.health}`">
                {{ healthLabel(projectDetail.health) }}
              </span>
            </div>
            <div class="next-action-block">
              <div class="overview-heading"><span>下一步行动</span><ListTodo :size="16" /></div>
              <p>{{ projectDetail.next_action || '尚未设置' }}</p>
            </div>
            <div v-if="projectDetail.description" class="description-block">
              <div class="overview-heading"><span>项目说明</span><AlignLeft :size="16" /></div>
              <p>{{ projectDetail.description }}</p>
            </div>
          </section>

          <div class="project-work-area">
            <a-tabs v-model:active-key="detailTab" class="detail-tabs">
              <a-tab-pane key="plan" tab="执行计划">
                <ResearchProjectPlan class="plan-panel" :project="projectDetail" @changed="handlePlanChanged" />
              </a-tab-pane>
              <a-tab-pane key="assets" tab="研究成果">
                <section class="assets-panel">
              <header class="section-heading">
                <div>
                  <h3>研究成果</h3>
                  <span>{{ projectDetail.asset_counts?.total || 0 }} 项</span>
                </div>
                <a-button
                  type="primary"
                  class="lucide-icon-btn"
                  :disabled="projectDetail.status !== 'active' || !canAddCurrentAssetType"
                  @click="openCandidateDrawer"
                >
                  <template #icon><Plus :size="15" /></template>
                  添加{{ currentAssetType.label }}
                </a-button>
              </header>

              <div class="asset-type-tabs" role="tablist" aria-label="研究成果类型">
                <button
                  v-for="option in assetTypeOptions"
                  :key="option.value"
                  type="button"
                  role="tab"
                  :aria-selected="assetType === option.value"
                  :class="{ active: assetType === option.value }"
                  @click="selectAssetType(option.value)"
                >
                  <component :is="option.icon" :size="15" />
                  <span>{{ option.shortLabel }}</span>
                  <em>{{ projectDetail.asset_counts?.[option.value] || 0 }}</em>
                </button>
              </div>

              <div class="asset-toolbar">
                <a-input-search
                  v-model:value="assetQuery"
                  allow-clear
                  :maxlength="500"
                  placeholder="搜索已归集成果"
                  @search="applyAssetSearch"
                />
              </div>

              <a-skeleton v-if="assetsLoading && !assets.length" active :paragraph="{ rows: 5 }" />
              <a-result v-else-if="assetsError" status="error" title="成果加载失败" :sub-title="assetsError">
                <template #extra><a-button @click="loadAssets">重新加载</a-button></template>
              </a-result>
              <div v-else-if="assets.length" class="asset-list">
                <article v-for="asset in assets" :key="asset.asset_id" class="asset-item" :class="{ unavailable: !asset.available }">
                  <div class="asset-icon"><component :is="currentAssetType.icon" :size="18" /></div>
                  <div class="asset-main">
                    <div class="asset-heading">
                      <strong>{{ asset.title }}</strong>
                      <span v-if="!asset.available" class="availability unavailable-label">源成果不可用</span>
                      <span v-else class="availability">{{ assetStatusLabel(asset.status) }}</span>
                    </div>
                    <p v-if="asset.summary">{{ asset.summary }}</p>
                    <div class="asset-meta">
                      <span>{{ assetMetadata(asset) }}</span>
                      <time>{{ formatDateTime(asset.added_at) }}</time>
                    </div>
                    <div v-if="asset.notes" class="asset-notes"><StickyNote :size="13" />{{ asset.notes }}</div>
                  </div>
                  <div class="asset-actions">
                    <a-button
                      type="link"
                      size="small"
                      :disabled="!asset.available"
                      @click="$emit('continue-asset', asset)"
                    >
                      继续研究
                      <ArrowRight :size="14" />
                    </a-button>
                    <a-tooltip title="编辑备注">
                      <a-button
                        type="text"
                        class="icon-only-action"
                        :disabled="projectDetail.status !== 'active'"
                        aria-label="编辑成果备注"
                        @click="openNotesModal(asset)"
                      >
                        <Pencil :size="14" />
                      </a-button>
                    </a-tooltip>
                    <a-popconfirm
                      title="从当前项目移除这项成果？源成果不会被删除。"
                      ok-text="移除"
                      cancel-text="取消"
                      :disabled="projectDetail.status !== 'active'"
                      @confirm="removeAsset(asset)"
                    >
                      <a-button
                        type="text"
                        danger
                        class="icon-only-action"
                        :disabled="projectDetail.status !== 'active'"
                        :loading="removingAssetId === asset.asset_id"
                        aria-label="移除项目成果"
                      >
                        <X :size="14" />
                      </a-button>
                    </a-popconfirm>
                  </div>
                </article>
              </div>
              <div v-else class="assets-empty">
                <component :is="currentAssetType.icon" :size="28" />
                <strong>暂无{{ currentAssetType.label }}</strong>
                <a-button
                  v-if="projectDetail.status === 'active' && canAddCurrentAssetType"
                  size="small"
                  @click="openCandidateDrawer"
                >
                  添加{{ currentAssetType.label }}
                </a-button>
              </div>

              <a-pagination
                v-if="assetsTotal > assetPageSize"
                v-model:current="assetPage"
                :page-size="assetPageSize"
                :total="assetsTotal"
                :show-size-changer="false"
                size="small"
                class="assets-pagination"
                @change="loadAssets"
              />
                </section>
              </a-tab-pane>

              <a-tab-pane key="activity" tab="最近活动">
                <aside class="activity-panel">
                  <header class="section-heading">
                    <div><h3>最近活动</h3><span>{{ projectDetail.activities?.length || 0 }} 条</span></div>
                  </header>
                  <ol v-if="projectDetail.activities?.length" class="activity-list">
                    <li v-for="activity in projectDetail.activities" :key="activity.activity_id">
                      <span class="activity-marker"><component :is="activityIcon(activity)" :size="13" /></span>
                      <div>
                        <strong>{{ activityLabel(activity) }}</strong>
                        <p v-if="activity.payload?.title">{{ activity.payload.title }}</p>
                        <time>{{ formatDateTime(activity.created_at) }}</time>
                      </div>
                    </li>
                  </ol>
                  <div v-else class="activity-empty">暂无活动记录</div>
                </aside>
              </a-tab-pane>
            </a-tabs>
          </div>
        </div>
        <div v-else class="detail-empty">
          <FolderOpen :size="38" />
          <strong>选择一个研究项目</strong>
        </div>
      </main>
    </div>

    <a-empty v-else description="请选择论文知识库" class="page-empty" />

    <a-modal
      v-model:open="projectModalOpen"
      :title="editingProject ? '编辑研究项目' : '新建研究项目'"
      width="720px"
      :confirm-loading="projectSaving"
      ok-text="保存"
      cancel-text="取消"
      @ok="saveProject"
    >
      <a-form layout="vertical" class="project-form">
        <a-form-item label="项目名称" required>
          <a-input
            v-model:value="projectForm.title"
            :disabled="editingProject && projectDetail?.status === 'completed'"
            :maxlength="255"
            show-count
          />
        </a-form-item>
        <a-form-item label="核心研究问题" required>
          <a-textarea
            v-model:value="projectForm.researchQuestion"
            :disabled="editingProject && projectDetail?.status === 'completed'"
            :rows="4"
            :maxlength="8000"
            show-count
          />
        </a-form-item>
        <a-form-item label="项目说明">
          <a-textarea v-model:value="projectForm.description" :rows="3" :maxlength="12000" show-count />
        </a-form-item>
        <div class="form-grid">
          <a-form-item label="目标日期">
            <a-input
              v-model:value="projectForm.targetDate"
              :disabled="editingProject && projectDetail?.status === 'completed'"
              type="date"
            />
          </a-form-item>
          <a-form-item label="标签">
            <a-select
              v-model:value="projectForm.tags"
              :disabled="editingProject && projectDetail?.status === 'completed'"
              mode="tags"
              :max-tag-count="4"
              :token-separators="[',', '，']"
              placeholder="输入后回车"
            />
          </a-form-item>
        </div>
        <a-form-item label="下一步行动">
          <a-textarea
            v-model:value="projectForm.nextAction"
            :disabled="editingProject && projectDetail?.status === 'completed'"
            :rows="2"
            :maxlength="4000"
            show-count
          />
        </a-form-item>
        <a-form-item
          v-if="editingProject && projectDetail?.status === 'active' && projectDetail?.progress_source !== 'tasks'"
          label="当前进度"
        >
          <div class="progress-editor">
            <a-slider v-model:value="projectForm.progress" :min="0" :max="100" />
            <a-input-number v-model:value="projectForm.progress" :min="0" :max="100" :formatter="value => `${value}%`" />
          </div>
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal
      v-model:open="notesModalOpen"
      title="编辑成果备注"
      :confirm-loading="notesSaving"
      ok-text="保存备注"
      cancel-text="取消"
      @ok="saveAssetNotes"
    >
      <a-textarea v-model:value="assetNotes" :rows="5" :maxlength="4000" show-count />
    </a-modal>

    <a-drawer
      v-model:open="candidateDrawerOpen"
      :title="`添加${currentAssetType.label}`"
      width="min(720px, 100vw)"
      class="candidate-drawer"
      @close="resetCandidateDrawer"
    >
      <div class="candidate-content">
        <a-input-search
          v-model:value="candidateQuery"
          allow-clear
          :maxlength="500"
          placeholder="搜索可归集成果"
          @search="searchCandidates"
        />
        <a-alert v-if="candidatesError" type="error" show-icon :message="candidatesError" />
        <a-skeleton v-if="candidatesLoading && !candidates.length" active :paragraph="{ rows: 6 }" />
        <div v-else-if="candidates.length" class="candidate-list">
          <label
            v-for="candidate in candidates"
            :key="candidate.reference_id"
            class="candidate-item"
            :class="{ linked: candidate.linked, selected: selectedCandidateIds.includes(candidate.reference_id) }"
          >
            <a-checkbox
              :checked="selectedCandidateIds.includes(candidate.reference_id)"
              :disabled="candidate.linked"
              @change="toggleCandidate(candidate.reference_id, $event.target.checked)"
            />
            <div>
              <div class="candidate-heading">
                <strong>{{ candidate.title }}</strong>
                <span v-if="candidate.linked">已归集</span>
                <span v-else>{{ assetStatusLabel(candidate.status) }}</span>
              </div>
              <p v-if="candidate.summary">{{ candidate.summary }}</p>
              <span class="candidate-meta">{{ candidateMetadata(candidate) }} · {{ formatDateTime(candidate.created_at) }}</span>
            </div>
          </label>
        </div>
        <div v-else class="candidate-empty">没有可归集的{{ currentAssetType.label }}</div>

        <a-pagination
          v-if="candidatesTotal > candidatePageSize"
          v-model:current="candidatePage"
          :page-size="candidatePageSize"
          :total="candidatesTotal"
          :show-size-changer="false"
          size="small"
          @change="loadCandidates"
        />

        <div class="candidate-notes">
          <label for="candidate-shared-notes">归集备注</label>
          <a-textarea id="candidate-shared-notes" v-model:value="candidateNotes" :rows="3" :maxlength="4000" show-count />
        </div>
      </div>
      <template #footer>
        <div class="drawer-footer">
          <span>已选择 {{ selectedCandidateIds.length }} 项</span>
          <div>
            <a-button @click="candidateDrawerOpen = false">取消</a-button>
            <a-button
              type="primary"
              :loading="candidatesAdding"
              :disabled="!selectedCandidateIds.length"
              @click="addSelectedCandidates"
            >
              添加到项目
            </a-button>
          </div>
        </div>
      </template>
    </a-drawer>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import {
  AlignLeft,
  Archive,
  ArrowRight,
  BookOpen,
  CalendarDays,
  ChevronDown,
  CircleCheck,
  Clock3,
  Download,
  FileSearch,
  FileText,
  Flag,
  FlaskConical,
  FolderKanban,
  FolderOpen,
  History,
  ListTodo,
  Paperclip,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  StickyNote,
  Trash2,
  X
} from 'lucide-vue-next'
import { researchApi } from '@/apis/research_api'
import { useUserStore } from '@/stores/user'
import ResearchProjectPlan from './ResearchProjectPlan.vue'

const props = defineProps({
  kbId: { type: String, default: '' },
  databaseOptions: { type: Array, default: () => [] },
  databasesLoading: { type: Boolean, default: false }
})

defineEmits(['change-database', 'continue-asset'])

const userStore = useUserStore()
const assetTypeOptions = [
  { value: 'paper', label: '论文', shortLabel: '论文', icon: BookOpen },
  { value: 'search_run', label: '检索运行', shortLabel: '检索', icon: Search },
  { value: 'synthesis_run', label: '证据综述', shortLabel: '综述', icon: FileText },
  { value: 'analysis_run', label: '论文分析', shortLabel: '分析', icon: FileSearch },
  { value: 'evaluation_experiment', label: '消融实验', shortLabel: '实验', icon: FlaskConical }
]
const statusFilterOptions = [
  { value: 'all', label: '全部' },
  { value: 'active', label: '进行中' },
  { value: 'completed', label: '已完成' },
  { value: 'archived', label: '已归档' }
]

const projects = ref([])
const projectsTotal = ref(0)
const projectsLoading = ref(false)
const projectPage = ref(1)
const projectPageSize = 20
const projectQuery = ref('')
const projectStatus = ref('all')
const selectedProjectId = ref('')
const projectDetail = ref(null)
const detailLoading = ref(false)
const workspaceError = ref('')
const projectModalOpen = ref(false)
const editingProject = ref(false)
const projectSaving = ref(false)
const projectDeleting = ref(false)
const statusUpdating = ref('')
const reportDownloading = ref(false)
const detailTab = ref('plan')
const assetType = ref('paper')
const assets = ref([])
const assetsTotal = ref(0)
const assetsLoading = ref(false)
const assetsError = ref('')
const assetPage = ref(1)
const assetPageSize = 10
const assetQuery = ref('')
const removingAssetId = ref('')
const notesModalOpen = ref(false)
const notesSaving = ref(false)
const editingAsset = ref(null)
const assetNotes = ref('')
const candidateDrawerOpen = ref(false)
const candidates = ref([])
const candidatesTotal = ref(0)
const candidatesLoading = ref(false)
const candidatesError = ref('')
const candidatesAdding = ref(false)
const candidateQuery = ref('')
const candidatePage = ref(1)
const candidatePageSize = 20
const candidateNotes = ref('')
const selectedCandidateIds = ref([])
let requestGeneration = 0
let projectRequestSequence = 0
let detailRequestSequence = 0
let assetRequestSequence = 0
let candidateRequestSequence = 0

const projectForm = reactive({
  title: '',
  researchQuestion: '',
  description: '',
  targetDate: '',
  tags: [],
  nextAction: '',
  progress: 0
})

const currentAssetType = computed(() => assetTypeOptions.find((item) => item.value === assetType.value) || assetTypeOptions[0])
const canAddCurrentAssetType = computed(() => (
  assetType.value !== 'evaluation_experiment' || userStore.isAdmin
))

const statusLabel = (status) => ({ active: '进行中', completed: '已完成', archived: '已归档' })[status] || status
const healthLabel = (health) => ({
  completed: '已完成', overdue: '已逾期', at_risk: '有风险', on_track: '进展正常', not_planned: '尚未规划'
})[health] || '尚未规划'
const assetStatusLabel = (status) => ({
  success: '已完成', completed: '已完成', verified: '已校正', extracted: '已提取',
  running: '运行中', pending: '等待中', queued: '等待中', failed: '失败', cancelled: '已取消'
})[status] || status || '状态未知'
const formatDateTime = (value) => {
  if (!value) return '时间未知'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString('zh-CN')
}
const formatCompactDate = (value) => {
  if (!value) return '时间未知'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}
const formatDate = (value) => {
  if (!value) return ''
  const date = new Date(`${value}T00:00:00`)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('zh-CN')
}
const assetMetadata = (asset) => {
  const metadata = asset.metadata || {}
  if (asset.asset_type === 'paper') return [metadata.publication_year, metadata.venue].filter(Boolean).join(' · ') || '论文成果'
  if (asset.asset_type === 'search_run') return `${metadata.result_count || 0} 篇结果 · ${metadata.mode || '检索模式未知'}`
  if (asset.asset_type === 'synthesis_run') return metadata.stage || '综述运行'
  if (asset.asset_type === 'analysis_run') return metadata.strategy || 'multi_agent'
  return `${metadata.completed_variants || 0}/${metadata.total_variants || 0} 个变体`
}
const candidateMetadata = (candidate) => assetMetadata({ asset_type: assetType.value, metadata: candidate.metadata || {} })
const activityLabel = (activity) => ({
  project_created: '创建项目',
  project_updated: '更新项目信息',
  project_status_changed: '变更项目状态',
  asset_added: '归集研究成果',
  asset_removed: '移除研究成果',
  asset_notes_updated: '更新成果备注',
  milestone_created: '创建里程碑',
  milestone_updated: '更新里程碑',
  milestone_deleted: '删除里程碑',
  milestone_status_changed: '变更里程碑状态',
  milestones_reordered: '调整里程碑顺序',
  task_created: '创建任务',
  task_updated: '更新任务',
  task_deleted: '删除任务',
  task_status_changed: '变更任务状态',
  tasks_reordered: '调整任务顺序',
  plan_asset_linked: '关联执行证据',
  plan_asset_unlinked: '解除执行证据'
})[activity.activity_type] || '更新项目'
const activityIcon = (activity) => ({
  project_created: FolderKanban,
  project_updated: Pencil,
  project_status_changed: CircleCheck,
  asset_added: Plus,
  asset_removed: X,
  asset_notes_updated: StickyNote,
  milestone_created: Flag,
  milestone_updated: Flag,
  milestone_deleted: Flag,
  milestone_status_changed: Flag,
  milestones_reordered: Flag,
  task_created: ListTodo,
  task_updated: ListTodo,
  task_deleted: ListTodo,
  task_status_changed: CircleCheck,
  tasks_reordered: ListTodo,
  plan_asset_linked: Paperclip,
  plan_asset_unlinked: Paperclip
})[activity.activity_type] || History

const loadProjects = async ({ preserveSelection = true } = {}) => {
  if (!props.kbId) return
  const requestSequence = ++projectRequestSequence
  const generation = requestGeneration
  const kbId = props.kbId
  projectsLoading.value = true
  workspaceError.value = ''
  try {
    const result = await researchApi.listProjects(kbId, {
      status: projectStatus.value === 'all' ? undefined : projectStatus.value,
      query: projectQuery.value.trim(),
      offset: (projectPage.value - 1) * projectPageSize,
      limit: projectPageSize
    })
    if (generation !== requestGeneration || kbId !== props.kbId || requestSequence !== projectRequestSequence) return
    projects.value = result.items || []
    projectsTotal.value = result.total || 0
    const maxPage = Math.max(1, Math.ceil(projectsTotal.value / projectPageSize))
    if (projectPage.value > maxPage) {
      projectPage.value = maxPage
      await loadProjects({ preserveSelection })
      return
    }
    const selectedExists = projects.value.some((item) => item.project_id === selectedProjectId.value)
    if (!preserveSelection || !selectedExists) {
      selectedProjectId.value = projects.value[0]?.project_id || ''
    }
    if (selectedProjectId.value) await loadProjectDetail()
    else projectDetail.value = null
  } catch (error) {
    if (generation !== requestGeneration || requestSequence !== projectRequestSequence) return
    workspaceError.value = error.message || '研究项目加载失败'
  } finally {
    if (generation === requestGeneration && requestSequence === projectRequestSequence) projectsLoading.value = false
  }
}

const loadProjectDetail = async () => {
  if (!selectedProjectId.value) return
  const requestSequence = ++detailRequestSequence
  const generation = requestGeneration
  const projectId = selectedProjectId.value
  detailLoading.value = true
  try {
    const detail = await researchApi.getProject(projectId)
    if (generation !== requestGeneration || projectId !== selectedProjectId.value || requestSequence !== detailRequestSequence) return
    projectDetail.value = detail
    await loadAssets()
  } catch (error) {
    if (generation !== requestGeneration || requestSequence !== detailRequestSequence) return
    workspaceError.value = error.message || '项目详情加载失败'
  } finally {
    if (generation === requestGeneration && requestSequence === detailRequestSequence) detailLoading.value = false
  }
}

const selectProject = async (projectId) => {
  if (projectId === selectedProjectId.value && projectDetail.value) return
  selectedProjectId.value = projectId
  assetType.value = 'paper'
  assetPage.value = 1
  assetQuery.value = ''
  detailTab.value = 'plan'
  projectDetail.value = null
  await loadProjectDetail()
}

const applyProjectFilters = () => {
  projectPage.value = 1
  void loadProjects({ preserveSelection: false })
}

const loadAssets = async () => {
  if (!selectedProjectId.value) return
  const requestSequence = ++assetRequestSequence
  const generation = requestGeneration
  const projectId = selectedProjectId.value
  assetsLoading.value = true
  assetsError.value = ''
  try {
    const result = await researchApi.listProjectAssets(projectId, {
      asset_type: assetType.value,
      query: assetQuery.value.trim(),
      offset: (assetPage.value - 1) * assetPageSize,
      limit: assetPageSize
    })
    if (generation !== requestGeneration || projectId !== selectedProjectId.value || requestSequence !== assetRequestSequence) return
    assets.value = result.items || []
    assetsTotal.value = result.total || 0
    const maxPage = Math.max(1, Math.ceil(assetsTotal.value / assetPageSize))
    if (assetPage.value > maxPage) {
      assetPage.value = maxPage
      await loadAssets()
    }
  } catch (error) {
    if (generation !== requestGeneration || requestSequence !== assetRequestSequence) return
    assetsError.value = error.message || '研究成果加载失败'
  } finally {
    if (generation === requestGeneration && requestSequence === assetRequestSequence) assetsLoading.value = false
  }
}

const selectAssetType = (value) => {
  assetType.value = value
  assetPage.value = 1
  assetQuery.value = ''
  assets.value = []
  void loadAssets()
}

const applyAssetSearch = () => {
  assetPage.value = 1
  void loadAssets()
}

const resetProjectForm = () => {
  Object.assign(projectForm, {
    title: '', researchQuestion: '', description: '', targetDate: '', tags: [], nextAction: '', progress: 0
  })
}

const openCreateProject = () => {
  editingProject.value = false
  resetProjectForm()
  projectModalOpen.value = true
}

const openEditProject = () => {
  if (!projectDetail.value) return
  editingProject.value = true
  Object.assign(projectForm, {
    title: projectDetail.value.title,
    researchQuestion: projectDetail.value.research_question,
    description: projectDetail.value.description || '',
    targetDate: projectDetail.value.target_date || '',
    tags: [...(projectDetail.value.tags || [])],
    nextAction: projectDetail.value.next_action || '',
    progress: projectDetail.value.progress || 0
  })
  projectModalOpen.value = true
}

const saveProject = async () => {
  const title = projectForm.title.trim()
  const researchQuestion = projectForm.researchQuestion.trim()
  if (!title || !researchQuestion) {
    message.warning('请填写项目名称和核心研究问题')
    return
  }
  const normalizedTags = [...new Set(projectForm.tags.map((tag) => String(tag).trim()))]
  if (normalizedTags.some((tag) => !tag || tag.length > 64)) {
    message.warning('每个标签需为 1 到 64 个字符')
    return
  }
  if (normalizedTags.length > 20) {
    message.warning('项目标签不能超过 20 个')
    return
  }
  projectSaving.value = true
  try {
    const payload = {
      title,
      research_question: researchQuestion,
      description: projectForm.description.trim(),
      tags: normalizedTags,
      target_date: projectForm.targetDate || null,
      next_action: projectForm.nextAction.trim()
    }
    let saved
    if (editingProject.value) {
      if (projectDetail.value?.status === 'completed') {
        saved = await researchApi.updateProject(selectedProjectId.value, {
          description: projectForm.description.trim()
        })
        message.success('项目说明已更新')
        projectModalOpen.value = false
        await loadProjects()
        return
      }
      if (projectDetail.value?.status === 'active' && projectDetail.value?.progress_source !== 'tasks') {
        payload.progress = projectForm.progress
      }
      saved = await researchApi.updateProject(selectedProjectId.value, payload)
      message.success('项目已更新')
    } else {
      saved = await researchApi.createProject(props.kbId, payload)
      selectedProjectId.value = saved.project_id
      projectStatus.value = 'all'
      projectQuery.value = ''
      projectPage.value = 1
      message.success('研究项目已创建')
    }
    projectModalOpen.value = false
    await loadProjects()
  } catch (error) {
    message.error(error.message || '项目保存失败')
  } finally {
    projectSaving.value = false
  }
}

const changeProjectStatus = async (status) => {
  if (!selectedProjectId.value) return
  statusUpdating.value = status
  try {
    await researchApi.updateProject(selectedProjectId.value, { status })
    message.success({ completed: '项目已完成', archived: '项目已归档', active: '项目已恢复' }[status])
    await loadProjects()
  } catch (error) {
    message.error(error.message || '项目状态更新失败')
  } finally {
    statusUpdating.value = ''
  }
}

const handlePlanChanged = async () => {
  await loadProjects()
}

const downloadReport = async ({ key }) => {
  if (!selectedProjectId.value) return
  reportDownloading.value = true
  try {
    const response = await researchApi.exportProjectReport(selectedProjectId.value, key)
    const blob = await response.blob()
    const disposition = response.headers.get('Content-Disposition') || ''
    const filename = disposition.match(/filename="?([^";]+)"?/)?.[1] || `research-project.${key === 'docx' ? 'docx' : 'md'}`
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
    message.success('项目报告已导出')
  } catch (error) {
    message.error(error.message || '项目报告导出失败')
  } finally {
    reportDownloading.value = false
  }
}

const deleteCurrentProject = async () => {
  if (!selectedProjectId.value) return
  projectDeleting.value = true
  try {
    await researchApi.deleteProject(selectedProjectId.value)
    message.success('研究项目已删除')
    selectedProjectId.value = ''
    projectDetail.value = null
    await loadProjects({ preserveSelection: false })
  } catch (error) {
    message.error(error.message || '项目删除失败')
  } finally {
    projectDeleting.value = false
  }
}

const openNotesModal = (asset) => {
  editingAsset.value = asset
  assetNotes.value = asset.notes || ''
  notesModalOpen.value = true
}

const saveAssetNotes = async () => {
  if (!editingAsset.value) return
  notesSaving.value = true
  try {
    const updated = await researchApi.updateProjectAsset(
      selectedProjectId.value,
      editingAsset.value.asset_id,
      { notes: assetNotes.value.trim() }
    )
    const index = assets.value.findIndex((item) => item.asset_id === updated.asset_id)
    if (index >= 0) assets.value[index] = updated
    notesModalOpen.value = false
    message.success('成果备注已更新')
    await loadProjectDetail()
  } catch (error) {
    message.error(error.message || '备注保存失败')
  } finally {
    notesSaving.value = false
  }
}

const removeAsset = async (asset) => {
  removingAssetId.value = asset.asset_id
  try {
    await researchApi.removeProjectAsset(selectedProjectId.value, asset.asset_id)
    message.success('成果已从项目移除')
    await loadProjectDetail()
  } catch (error) {
    message.error(error.message || '成果移除失败')
  } finally {
    removingAssetId.value = ''
  }
}

const openCandidateDrawer = () => {
  candidateDrawerOpen.value = true
  candidatePage.value = 1
  candidateQuery.value = ''
  candidateNotes.value = ''
  selectedCandidateIds.value = []
  candidates.value = []
  void loadCandidates()
}

const loadCandidates = async () => {
  if (!selectedProjectId.value) return
  const requestSequence = ++candidateRequestSequence
  const generation = requestGeneration
  const projectId = selectedProjectId.value
  const currentType = assetType.value
  candidatesLoading.value = true
  candidatesError.value = ''
  try {
    const result = await researchApi.listProjectAssetCandidates(projectId, {
      asset_type: currentType,
      query: candidateQuery.value.trim(),
      offset: (candidatePage.value - 1) * candidatePageSize,
      limit: candidatePageSize
    })
    if (generation !== requestGeneration || projectId !== selectedProjectId.value || currentType !== assetType.value || requestSequence !== candidateRequestSequence) return
    candidates.value = result.items || []
    candidatesTotal.value = result.total || 0
  } catch (error) {
    if (generation !== requestGeneration || projectId !== selectedProjectId.value || currentType !== assetType.value || requestSequence !== candidateRequestSequence) return
    candidatesError.value = error.message || '可归集成果加载失败'
  } finally {
    if (generation === requestGeneration && projectId === selectedProjectId.value && currentType === assetType.value && requestSequence === candidateRequestSequence) {
      candidatesLoading.value = false
    }
  }
}

const searchCandidates = () => {
  candidatePage.value = 1
  void loadCandidates()
}

const toggleCandidate = (referenceId, checked) => {
  if (checked && !selectedCandidateIds.value.includes(referenceId)) {
    if (selectedCandidateIds.value.length >= 100) {
      message.warning('单次最多归集 100 项成果')
      return
    }
    selectedCandidateIds.value.push(referenceId)
  } else if (!checked) {
    selectedCandidateIds.value = selectedCandidateIds.value.filter((item) => item !== referenceId)
  }
}

const addSelectedCandidates = async () => {
  candidatesAdding.value = true
  try {
    await researchApi.addProjectAssets(selectedProjectId.value, {
      asset_type: assetType.value,
      reference_ids: selectedCandidateIds.value,
      notes: candidateNotes.value.trim()
    })
    message.success(`已添加 ${selectedCandidateIds.value.length} 项成果`)
    candidateDrawerOpen.value = false
    await loadProjectDetail()
  } catch (error) {
    message.error(error.message || '成果归集失败')
  } finally {
    candidatesAdding.value = false
  }
}

const resetCandidateDrawer = () => {
  candidates.value = []
  candidatesTotal.value = 0
  candidatesError.value = ''
  selectedCandidateIds.value = []
}

const resetWorkspace = () => {
  requestGeneration += 1
  projectRequestSequence += 1
  detailRequestSequence += 1
  assetRequestSequence += 1
  candidateRequestSequence += 1
  projects.value = []
  projectsTotal.value = 0
  selectedProjectId.value = ''
  projectDetail.value = null
  assets.value = []
  assetsTotal.value = 0
  projectsLoading.value = false
  detailLoading.value = false
  assetsLoading.value = false
  candidatesLoading.value = false
  workspaceError.value = ''
  projectPage.value = 1
  projectStatus.value = 'all'
  detailTab.value = 'plan'
  projectQuery.value = ''
  candidateDrawerOpen.value = false
  if (props.kbId) void loadProjects({ preserveSelection: false })
}

watch(() => props.kbId, resetWorkspace, { immediate: true })
onBeforeUnmount(() => { requestGeneration += 1 })
</script>

<style scoped lang="less">
.project-workspace {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 0;
}

.project-toolbar {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 12px;
}

.toolbar-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  label { color: var(--color-text-secondary); font-size: 12px; font-weight: 500; }
}

.database-field { width: min(420px, 100%); }

.project-layout {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  min-height: 620px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  overflow: hidden;
  background: var(--gray-0);
}

.project-sidebar {
  display: flex;
  flex-direction: column;
  min-width: 0;
  border-right: 1px solid var(--gray-150);
  background: var(--gray-10);
}

.sidebar-header,
.section-heading,
.project-detail-header,
.overview-heading,
.project-actions,
.project-meta,
.asset-heading,
.asset-meta,
.asset-actions,
.candidate-heading,
.drawer-footer,
.project-item-heading,
.project-item-meta {
  display: flex;
  align-items: center;
}

.sidebar-header {
  justify-content: space-between;
  padding: 16px;
  border-bottom: 1px solid var(--gray-150);
  h2 { margin: 0; color: var(--color-text); font-size: 17px; font-weight: 600; }
  span { color: var(--color-text-tertiary); font-size: 12px; }
}

.square-action,
.icon-only-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  min-width: 34px;
  padding: 0;
}

.project-filters { display: flex; flex-direction: column; gap: 10px; padding: 12px; }
.project-filters :deep(.ant-segmented-item-label) { min-height: 28px; padding-inline: 7px; font-size: 12px; }
.sidebar-loading { padding: 12px; }
.project-list { display: flex; flex: 1; flex-direction: column; gap: 6px; min-height: 0; padding: 0 8px 12px; }

.project-list-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  padding: 12px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text);
  text-align: left;
  cursor: pointer;
  &:hover { background: var(--gray-25); border-color: var(--gray-150); }
  &:focus-visible { outline: 2px solid var(--main-color); outline-offset: 1px; }
  &.active { background: var(--main-30); border-color: var(--main-200); }
}

.project-item-heading { justify-content: space-between; gap: 8px; }
.project-item-heading strong { min-width: 0; overflow: hidden; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.project-item-question { display: -webkit-box; overflow: hidden; color: var(--color-text-secondary); font-size: 12px; line-height: 1.45; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.project-status { flex: none; padding: 2px 7px; border-radius: 999px; font-size: 11px; font-style: normal; font-weight: 500; }
.status-active { background: var(--color-info-50); color: var(--color-info-700); }
.status-completed { background: var(--color-success-50); color: var(--color-success-700); }
.status-archived { background: var(--gray-100); color: var(--gray-600); }
.project-item-progress { display: grid; grid-template-columns: minmax(0, 1fr) 34px; gap: 8px; align-items: center; }
.project-item-progress > span { height: 4px; overflow: hidden; border-radius: 999px; background: var(--gray-150); }
.project-item-progress > span > span { display: block; height: 100%; border-radius: inherit; background: var(--main-color); }
.project-item-progress em { color: var(--color-text-tertiary); font-size: 11px; font-style: normal; text-align: right; }
.project-item-meta { justify-content: space-between; color: var(--color-text-tertiary); font-size: 11px; }
.project-item-meta span { display: inline-flex; align-items: center; gap: 4px; }
.health-label { display: inline-flex; align-items: center; gap: 4px; color: var(--color-text-secondary); font-size: 11px; font-weight: 500; }
.health-label::before { width: 6px; height: 6px; border-radius: 50%; background: var(--gray-400); content: ''; }
.health-on_track::before,
.health-completed::before { background: var(--color-success-500); }
.health-at_risk::before { background: var(--color-warning-500); }
.health-overdue::before { background: var(--color-error-500); }
.project-list-empty,
.detail-empty,
.assets-empty { display: flex; flex: 1; flex-direction: column; align-items: center; justify-content: center; gap: 10px; min-height: 220px; color: var(--color-text-tertiary); }
.project-list-empty strong,
.detail-empty strong,
.assets-empty strong { color: var(--color-text-secondary); font-size: 14px; }
.sidebar-pagination { margin: auto 12px 14px; }

.project-detail { min-width: 0; background: var(--gray-0); }
.detail-content { display: flex; flex-direction: column; min-width: 0; }
.detail-skeleton { padding: 24px; }
.project-detail-header { align-items: flex-start; justify-content: space-between; gap: 24px; padding: 22px 24px 18px; border-bottom: 1px solid var(--gray-150); }
.project-title-block { min-width: 0; }
.project-title-line { display: flex; align-items: center; gap: 10px; }
.project-title-line h2 { min-width: 0; margin: 0; overflow-wrap: anywhere; color: var(--color-text); font-size: 21px; font-weight: 600; }
.project-title-block > p { max-width: 900px; margin: 10px 0; color: var(--color-text-secondary); font-size: 14px; line-height: 1.6; white-space: pre-wrap; }
.project-meta { flex-wrap: wrap; gap: 8px; color: var(--color-text-tertiary); font-size: 12px; }
.project-meta > span { display: inline-flex; align-items: center; gap: 5px; }
.project-actions { flex: none; flex-wrap: wrap; justify-content: flex-end; gap: 8px; max-width: 460px; }

.project-overview { display: grid; grid-template-columns: minmax(220px, .7fr) minmax(260px, 1fr) minmax(280px, 1.2fr); border-bottom: 1px solid var(--gray-150); background: var(--gray-10); }
.project-overview > div { min-width: 0; padding: 16px 20px; border-right: 1px solid var(--gray-150); }
.project-overview > div:last-child { border-right: 0; }
.overview-heading { justify-content: space-between; gap: 10px; margin-bottom: 9px; color: var(--color-text-tertiary); font-size: 12px; }
.overview-heading strong { color: var(--main-700); font-size: 14px; }
.overview-health { margin-top: 3px; }
.next-action-block p,
.description-block p { margin: 0; color: var(--color-text); font-size: 13px; line-height: 1.55; white-space: pre-wrap; }

.project-work-area { min-width: 0; }
.detail-tabs :deep(.ant-tabs-nav) { margin: 0; padding: 0 24px; border-bottom: 1px solid var(--gray-150); }
.detail-tabs :deep(.ant-tabs-tab) { padding-block: 13px; }
.plan-panel { padding: 20px 24px 24px; }
.assets-panel { min-width: 0; padding: 20px 24px 24px; }
.activity-panel { min-width: 0; padding: 20px 24px 24px; }
.section-heading { justify-content: space-between; gap: 16px; margin-bottom: 15px; }
.section-heading h3 { margin: 0; color: var(--color-text); font-size: 16px; font-weight: 600; }
.section-heading span { color: var(--color-text-tertiary); font-size: 12px; }

.asset-type-tabs { display: grid; grid-template-columns: repeat(5, minmax(72px, 1fr)); gap: 4px; padding: 4px; border: 1px solid var(--gray-150); border-radius: 6px; background: var(--gray-25); }
.asset-type-tabs button { display: flex; align-items: center; justify-content: center; gap: 5px; min-width: 0; min-height: 34px; padding: 4px 7px; border: 0; border-radius: 4px; background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.asset-type-tabs button:hover { background: var(--gray-0); color: var(--color-text); }
.asset-type-tabs button.active { background: var(--gray-0); color: var(--main-700); box-shadow: 0 1px 2px var(--shadow-1); }
.asset-type-tabs em { padding: 1px 5px; border-radius: 999px; background: var(--gray-100); color: var(--color-text-tertiary); font-size: 10px; font-style: normal; }
.asset-toolbar { display: flex; justify-content: flex-end; margin: 12px 0; }
.asset-toolbar :deep(.ant-input-search) { width: min(320px, 100%); }
.asset-list { border-top: 1px solid var(--gray-150); }
.asset-item { display: grid; grid-template-columns: 38px minmax(0, 1fr) auto; gap: 12px; align-items: start; padding: 15px 0; border-bottom: 1px solid var(--gray-150); }
.asset-item.unavailable { opacity: .76; }
.asset-icon { display: flex; align-items: center; justify-content: center; width: 36px; height: 36px; border-radius: 6px; background: var(--main-40); color: var(--main-700); }
.asset-main { min-width: 0; }
.asset-heading { align-items: flex-start; gap: 8px; }
.asset-heading strong { min-width: 0; color: var(--color-text); font-size: 14px; overflow-wrap: anywhere; }
.availability { flex: none; padding: 1px 6px; border-radius: 999px; background: var(--color-success-50); color: var(--color-success-700); font-size: 10px; }
.unavailable-label { background: var(--color-error-50); color: var(--color-error-700); }
.asset-main > p { display: -webkit-box; margin: 6px 0; overflow: hidden; color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.asset-meta { flex-wrap: wrap; gap: 8px; color: var(--color-text-tertiary); font-size: 11px; }
.asset-notes { display: flex; align-items: flex-start; gap: 5px; margin-top: 8px; padding: 7px 9px; border-radius: 4px; background: var(--gray-25); color: var(--color-text-secondary); font-size: 12px; line-height: 1.45; }
.asset-notes svg { flex: none; margin-top: 2px; }
.asset-actions { align-self: center; gap: 2px; }
.asset-actions :deep(.ant-btn-link) { display: inline-flex; align-items: center; gap: 4px; padding-inline: 5px; }
.assets-pagination { margin-top: 16px; text-align: right; }

.activity-list { margin: 0; padding: 0; list-style: none; }
.activity-list li { position: relative; display: grid; grid-template-columns: 28px minmax(0, 1fr); gap: 8px; padding-bottom: 18px; }
.activity-list li:not(:last-child)::before { position: absolute; top: 27px; bottom: 2px; left: 13px; width: 1px; background: var(--gray-200); content: ''; }
.activity-marker { z-index: 1; display: flex; align-items: center; justify-content: center; width: 27px; height: 27px; border: 1px solid var(--gray-150); border-radius: 50%; background: var(--gray-0); color: var(--main-700); }
.activity-list strong { display: block; color: var(--color-text); font-size: 12px; font-weight: 500; }
.activity-list p { display: -webkit-box; margin: 3px 0; overflow: hidden; color: var(--color-text-secondary); font-size: 11px; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.activity-list time { color: var(--color-text-tertiary); font-size: 10px; }
.activity-empty { padding: 28px 0; color: var(--color-text-tertiary); font-size: 12px; text-align: center; }

.project-form :deep(.ant-form-item) { margin-bottom: 16px; }
.form-grid { display: grid; grid-template-columns: 200px minmax(0, 1fr); gap: 16px; }
.progress-editor { display: grid; grid-template-columns: minmax(0, 1fr) 90px; gap: 16px; align-items: center; }

.candidate-content { display: flex; flex-direction: column; gap: 14px; }
.candidate-list { border-top: 1px solid var(--gray-150); }
.candidate-item { display: grid; grid-template-columns: 24px minmax(0, 1fr); gap: 10px; padding: 13px 4px; border-bottom: 1px solid var(--gray-150); cursor: pointer; }
.candidate-item:hover { background: var(--gray-10); }
.candidate-item.selected { background: var(--main-30); }
.candidate-item.linked { cursor: default; opacity: .6; }
.candidate-heading { align-items: flex-start; justify-content: space-between; gap: 12px; }
.candidate-heading strong { min-width: 0; color: var(--color-text); font-size: 13px; overflow-wrap: anywhere; }
.candidate-heading span { flex: none; color: var(--color-text-tertiary); font-size: 11px; }
.candidate-item p { display: -webkit-box; margin: 5px 0; overflow: hidden; color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.candidate-meta { color: var(--color-text-tertiary); font-size: 11px; }
.candidate-empty { padding: 48px 0; color: var(--color-text-tertiary); text-align: center; }
.candidate-notes { display: flex; flex-direction: column; gap: 6px; padding-top: 4px; }
.candidate-notes label { color: var(--color-text-secondary); font-size: 12px; font-weight: 500; }
.drawer-footer { justify-content: space-between; gap: 12px; width: 100%; }
.drawer-footer > span { color: var(--color-text-secondary); font-size: 12px; }
.drawer-footer > div { display: flex; gap: 8px; }

@media (max-width: 1280px) {
  .project-layout { grid-template-columns: 260px minmax(0, 1fr); }
  .project-overview { grid-template-columns: 1fr 1fr; }
  .description-block { grid-column: 1 / -1; border-top: 1px solid var(--gray-150); }
  .activity-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
  .activity-list li:nth-last-child(-n + 2) { padding-bottom: 0; }
}

@media (max-width: 960px) {
  .project-layout { grid-template-columns: 1fr; }
  .project-sidebar { max-height: 420px; border-right: 0; border-bottom: 1px solid var(--gray-150); }
  .project-list { overflow: auto; }
  .project-detail-header { flex-direction: column; }
  .project-actions { justify-content: flex-start; max-width: none; }
}

@media (max-width: 640px) {
  .project-toolbar { align-items: stretch; }
  .project-toolbar .lucide-icon-btn { align-self: end; }
  .project-layout { min-height: 0; }
  .project-detail-header,
  .assets-panel,
  .activity-panel,
  .plan-panel { padding: 16px; }
  .detail-tabs :deep(.ant-tabs-nav) { padding-inline: 16px; }
  .project-title-line { align-items: flex-start; flex-direction: column; }
  .project-title-line h2 { font-size: 18px; }
  .project-actions { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); width: 100%; }
  .project-actions :deep(.ant-btn) { width: 100%; }
  .project-actions > .icon-only-action { grid-column: 1 / -1; justify-self: end; width: 42px; }
  .project-overview { grid-template-columns: 1fr; }
  .project-overview > div { border-right: 0; border-bottom: 1px solid var(--gray-150); }
  .project-overview > div:last-child { border-bottom: 0; }
  .description-block { grid-column: auto; border-top: 0; }
  .asset-type-tabs { grid-template-columns: repeat(3, minmax(72px, 1fr)); }
  .asset-type-tabs button { min-height: 40px; }
  .section-heading { align-items: flex-start; }
  .asset-item { grid-template-columns: 34px minmax(0, 1fr); }
  .asset-actions { grid-column: 2; justify-content: flex-start; flex-wrap: wrap; }
  .activity-list { grid-template-columns: 1fr; }
  .activity-list li { padding-bottom: 16px !important; }
  .form-grid { grid-template-columns: 1fr; gap: 0; }
  .progress-editor { grid-template-columns: minmax(0, 1fr) 82px; }
}
</style>
