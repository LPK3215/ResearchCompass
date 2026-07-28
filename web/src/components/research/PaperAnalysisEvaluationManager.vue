<template>
  <section class="analysis-evaluation-manager">
    <header class="manager-header">
      <div>
        <h3>单 / 多 Agent 分析对比</h3>
        <p>固定同一模型和论文集，先生成两种报告，再由评审者以 A/B 盲审完成四维量表评分。</p>
      </div>
      <a-button v-if="isAdmin" type="primary" :disabled="papers.length < 2" @click="openCreate">新建对比</a-button>
    </header>

    <a-alert
      type="info"
      show-icon
      message="实验边界"
      description="系统不以模型自评代替人工结论。报告聚合只统计已经提交的人工盲审；没有真实评分时不会推断单 Agent 或多 Agent 更优。"
    />

    <a-table
      :loading="loading"
      :data-source="evaluations"
      :columns="columns"
      row-key="evaluation_id"
      :pagination="false"
      size="small"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'status'">
          <a-tag :color="statusColor(record.status)">{{ statusText(record.status) }}</a-tag>
        </template>
        <template v-else-if="column.key === 'progress'">{{ record.completed_pairs }}/{{ record.paper_count }}</template>
        <template v-else-if="column.key === 'actions'">
          <a-space>
            <a v-if="isAdmin" @click="openReport(record)">汇总</a>
            <a :class="{ disabled: !readyForReview(record) }" @click="openBlindReview(record)">盲审</a>
          </a-space>
        </template>
      </template>
    </a-table>

    <a-modal v-model:open="createOpen" title="新建单 / 多 Agent 对比" width="760px" :confirm-loading="creating" @ok="createEvaluation">
      <a-alert
        type="warning"
        show-icon
        message="请只选择已完成学术分块、且适合人工核查的论文"
        description="每篇论文会按相同 temperature=0 模型分别生成单 Agent 与四阶段多 Agent 报告；评审界面不展示策略名称。"
        class="form-alert"
      />
      <a-form layout="vertical">
        <div class="form-grid">
          <a-form-item label="实验名称" required><a-input v-model:value="form.name" :maxlength="255" /></a-form-item>
          <a-form-item label="固定模型" required><ModelSelectorComponent v-model:model_spec="form.model_spec" size="small" displayName="mini" /></a-form-item>
        </div>
        <a-form-item label="实验说明"><a-textarea v-model:value="form.description" :rows="2" :maxlength="4000" /></a-form-item>
        <a-form-item label="论文集（2–20 篇）" required>
          <a-select v-model:value="form.paper_ids" mode="multiple" :max-tag-count="5" :options="paperOptions" :loading="papersLoading" />
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:open="reportOpen" :title="`对比汇总 · ${selectedEvaluation?.name || ''}`" width="880px" :footer="null">
      <a-spin :spinning="reportLoading">
        <template v-if="report">
          <a-alert v-if="report.error_message" type="error" show-icon :message="report.error_message" class="form-alert" />
          <div class="report-meta">
            <a-tag :color="statusColor(report.status)">{{ statusText(report.status) }}</a-tag>
            <span>报告对：{{ report.completed_pairs }}/{{ report.paper_count }}</span>
            <span>真实盲审：{{ report.score_count }} 份</span>
            <span>量表：{{ report.rubric?.version }}</span>
            <span>固定模型：{{ report.model_config?.model }}</span>
          </div>
          <a-table :data-source="summaryRows" :columns="summaryColumns" row-key="strategy" :pagination="false" size="small" />
          <div class="preference-row">
            <span>偏好：单 Agent {{ report.preference_counts?.single_agent || 0 }}</span>
            <span>多 Agent {{ report.preference_counts?.multi_agent || 0 }}</span>
            <span>平局 {{ report.preference_counts?.tie || 0 }}</span>
          </div>
          <a-empty v-if="report.score_count === 0" description="尚未收到真实人工盲审，暂不输出优劣结论" />
        </template>
      </a-spin>
    </a-modal>

    <a-modal v-model:open="reviewOpen" :title="`盲审评分 · ${selectedEvaluation?.name || ''}`" width="960px" :footer="null">
      <a-spin :spinning="reviewLoading">
        <template v-if="blindItems.length">
          <a-tabs v-model:active-key="selectedBlindItemId" @change="loadBlindItem">
            <a-tab-pane v-for="item in blindItems" :key="item.item_id" :tab="item.paper.title" />
          </a-tabs>
          <template v-if="blindItem">
            <a-alert type="info" show-icon message="请依据论文原文核查两份报告；A/B 的策略身份在本轮评分后才会在管理员汇总中揭示。">
              <template #description>
                <a-button type="link" size="small" class="paper-source-link" @click="openBlindPaper">打开论文详情与原文</a-button>
              </template>
            </a-alert>
            <div class="blind-reports">
              <article v-for="label in ['A', 'B']" :key="label" class="blind-report">
                <h4>报告 {{ label }}</h4>
                <div class="report-overview">
                  <div><span>研究问题</span><p>{{ blindItem.reports[label]?.structure?.problem || '未明确' }}</p></div>
                  <div><span>核心方法</span><p>{{ blindItem.reports[label]?.structure?.method || '未明确' }}</p></div>
                  <div><span>主要结果</span><p>{{ asText(blindItem.reports[label]?.structure?.results) }}</p></div>
                  <div><span>局限性</span><p>{{ asText(blindItem.reports[label]?.structure?.limitations) }}</p></div>
                  <div><span>创新点</span><ul><li v-for="item in blindItem.reports[label]?.innovations?.items || []" :key="item.claim">{{ item.claim }}<small v-if="item.evidence">（{{ item.evidence }}）</small></li></ul></div>
                  <div><span>方法设计</span><p>{{ blindItem.reports[label]?.methodology?.research_design || '未明确' }}</p></div>
                  <div><span>研究空白</span><ul><li v-for="item in blindItem.reports[label]?.research_gaps?.gaps || []" :key="item.claim">{{ item.claim }}<small v-if="item.basis">（{{ item.basis }}）</small></li></ul></div>
                </div>
                <div v-for="dimension in rubricDimensions" :key="dimension.key" class="score-row">
                  <span>{{ dimension.label }}</span>
                  <a-radio-group v-model:value="blindScores[label][dimension.key]">
                    <a-radio v-for="score in [1, 2, 3, 4, 5]" :key="score" :value="score">{{ score }}</a-radio>
                  </a-radio-group>
                </div>
              </article>
            </div>
            <a-form layout="vertical" class="review-form">
              <a-form-item label="综合偏好（可选平局）">
                <a-radio-group v-model:value="blindScores.preference"><a-radio value="A">报告 A</a-radio><a-radio value="B">报告 B</a-radio><a-radio value="tie">平局</a-radio></a-radio-group>
              </a-form-item>
              <a-form-item label="核查说明（可选）"><a-textarea v-model:value="blindNotes" :rows="3" :maxlength="4000" /></a-form-item>
              <a-button type="primary" :loading="submitting" @click="submitBlindScore">提交或更新本人的盲审评分</a-button>
            </a-form>
          </template>
        </template>
        <a-empty v-else description="报告对尚未生成完成，暂不能开始盲审" />
      </a-spin>
    </a-modal>
  </section>
</template>

<script setup>
// ResearchCompass 单 / 多 Agent 分析对比管理器
// 本组件是本仓库在开源智能体框架 Yuxi 之上实现的科研业务界面，负责创建和管理
// 单 Agent 与四阶段多 Agent 报告的盲审对比实验：固定模型与论文集生成两份报告，
// 评审者以 A/B 盲审完成四维量表评分，汇总只统计真实人工评分。通用对话框、表格
// 等基础组件与智能体运行时由 Yuxi 提供。
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { researchApi } from '@/apis/research_api'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'
import { useUserStore } from '@/stores/user'

const props = defineProps({ kbId: { type: String, required: true } })
const emit = defineEmits(['open-paper'])
const userStore = useUserStore()
const isAdmin = computed(() => userStore.isAdmin)
const loading = ref(false)
const papersLoading = ref(false)
const creating = ref(false)
const evaluations = ref([])
const papers = ref([])
const createOpen = ref(false)
const reportOpen = ref(false)
const reportLoading = ref(false)
const reviewOpen = ref(false)
const reviewLoading = ref(false)
const submitting = ref(false)
const selectedEvaluation = ref(null)
const report = ref(null)
const blindItems = ref([])
const selectedBlindItemId = ref('')
const blindItem = ref(null)
const blindNotes = ref('')
const blindScores = reactive({ A: {}, B: {}, preference: 'tie' })
let evaluationTimer = null

const rubricDimensions = [
  { key: 'evidence_faithfulness', label: '证据忠实度' },
  { key: 'coverage', label: '要点覆盖度' },
  { key: 'methodological_accuracy', label: '方法准确性' },
  { key: 'research_utility', label: '科研实用性' }
]
const form = reactive({ name: '', description: '', model_spec: '', paper_ids: [] })
const columns = [
  { title: '实验', dataIndex: 'name', key: 'name', ellipsis: true },
  { title: '状态', key: 'status', width: 120 },
  { title: '报告对', key: 'progress', width: 100 },
  { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 180 },
  { title: '操作', key: 'actions', width: 120 }
]
const summaryColumns = [
  { title: '策略', dataIndex: 'strategy', key: 'strategy' },
  { title: '评分样本', dataIndex: 'score_count', key: 'score_count' },
  { title: '证据忠实度', dataIndex: 'evidence_faithfulness', key: 'evidence_faithfulness' },
  { title: '覆盖度', dataIndex: 'coverage', key: 'coverage' },
  { title: '方法准确性', dataIndex: 'methodological_accuracy', key: 'methodological_accuracy' },
  { title: '科研实用性', dataIndex: 'research_utility', key: 'research_utility' },
  { title: '总体均值', dataIndex: 'overall_mean', key: 'overall_mean' },
  { title: '平均耗时', dataIndex: 'duration_mean_ms', key: 'duration_mean_ms' }
]
const paperOptions = computed(() => papers.value.map((paper) => ({ value: paper.paper_id, label: paper.title })))
const summaryRows = computed(() => ['single_agent', 'multi_agent'].map((strategy) => {
  const summary = report.value?.strategy_summary?.[strategy] || {}
  const dimensions = summary.dimension_means || {}
  return {
    strategy: strategy === 'single_agent' ? '单 Agent' : '四阶段多 Agent',
    score_count: summary.score_count || 0,
    ...dimensions,
    overall_mean: summary.overall_mean ?? '-',
    duration_mean_ms: summary.duration_mean_ms === null || summary.duration_mean_ms === undefined
      ? '-'
      : `${summary.duration_mean_ms} ms`
  }
}))
const statusText = (status) => ({ queued: '等待中', running: '生成中', completed: '已完成', completed_with_failures: '部分失败', failed: '失败' }[status] || status)
const statusColor = (status) => ({ queued: 'default', running: 'processing', completed: 'success', completed_with_failures: 'warning', failed: 'error' }[status] || 'default')
const readyForReview = (item) => item.completed_pairs > 0
const asText = (value) => Array.isArray(value) ? value.join('；') : String(value || '未明确')

const resetScoreForm = () => {
  Object.assign(blindScores, { A: {}, B: {}, preference: 'tie' })
  blindNotes.value = ''
}
const loadPapers = async () => {
  papersLoading.value = true
  try { papers.value = (await researchApi.listPapers(props.kbId, { page: 1, page_size: 200 })).items || [] } catch (error) { message.error(error.message || '加载论文失败') } finally { papersLoading.value = false }
}
const syncEvaluationRefresh = () => {
  const hasRunning = evaluations.value.some((item) => ['queued', 'running'].includes(item.status))
  if (hasRunning && !evaluationTimer) {
    evaluationTimer = window.setInterval(() => loadEvaluations(true), 2500)
  } else if (!hasRunning && evaluationTimer) {
    window.clearInterval(evaluationTimer)
    evaluationTimer = null
  }
}
const loadEvaluations = async (silent = false) => {
  if (!silent) loading.value = true
  try {
    evaluations.value = await researchApi.listPaperAnalysisEvaluations(props.kbId)
  } catch (error) {
    if (!silent) message.error(error.message || '加载分析对比失败')
  } finally {
    loading.value = false
  }
  syncEvaluationRefresh()
}
const openCreate = () => { Object.assign(form, { name: '', description: '', model_spec: '', paper_ids: [] }); createOpen.value = true }
const createEvaluation = async () => {
  if (!form.name.trim() || !form.model_spec.trim() || form.paper_ids.length < 2) return message.warning('请填写实验名称、固定模型并选择至少两篇论文')
  creating.value = true
  try {
    await researchApi.createPaperAnalysisEvaluation(props.kbId, { ...form, name: form.name.trim(), description: form.description.trim() })
    message.success('已提交单 / 多 Agent 报告对比')
    createOpen.value = false
    await loadEvaluations()
  } catch (error) { message.error(error.message || '创建分析对比失败') } finally { creating.value = false }
}
const openReport = async (evaluation) => {
  selectedEvaluation.value = evaluation; reportOpen.value = true; reportLoading.value = true
  try { report.value = await researchApi.getPaperAnalysisEvaluation(props.kbId, evaluation.evaluation_id) } catch (error) { message.error(error.message || '加载汇总失败') } finally { reportLoading.value = false }
}
const openBlindReview = async (evaluation) => {
  if (!readyForReview(evaluation)) return
  selectedEvaluation.value = evaluation; reviewOpen.value = true; reviewLoading.value = true; blindItems.value = []; blindItem.value = null
  try {
    blindItems.value = await researchApi.listPaperAnalysisBlindItems(props.kbId, evaluation.evaluation_id)
    selectedBlindItemId.value = blindItems.value[0]?.item_id || ''
    if (selectedBlindItemId.value) await loadBlindItem()
  } catch (error) { message.error(error.message || '加载盲审条目失败') } finally { reviewLoading.value = false }
}
const loadBlindItem = async () => {
  if (!selectedBlindItemId.value || !selectedEvaluation.value) return
  reviewLoading.value = true
  try {
    blindItem.value = await researchApi.getPaperAnalysisBlindItem(props.kbId, selectedEvaluation.value.evaluation_id, selectedBlindItemId.value)
    resetScoreForm()
    if (blindItem.value.own_score) Object.assign(blindScores, blindItem.value.own_score)
    blindNotes.value = blindItem.value.own_notes || ''
  } catch (error) { message.error(error.message || '加载盲审报告失败') } finally { reviewLoading.value = false }
}
const submitBlindScore = async () => {
  if (!blindItem.value || !selectedEvaluation.value) return
  const incomplete = ['A', 'B'].some((label) => rubricDimensions.some(({ key }) => !Number.isInteger(blindScores[label][key])))
  if (incomplete) return message.warning('请完成 A 和 B 的全部量表评分')
  submitting.value = true
  try {
    await researchApi.submitPaperAnalysisBlindScore(props.kbId, selectedEvaluation.value.evaluation_id, blindItem.value.item_id, { blind_scores: JSON.parse(JSON.stringify(blindScores)), notes: blindNotes.value })
    message.success('盲审评分已保存')
    await Promise.all([loadBlindItem(), loadEvaluations()])
  } catch (error) { message.error(error.message || '提交盲审评分失败') } finally { submitting.value = false }
}
const openBlindPaper = () => {
  const paperId = blindItem.value?.paper?.paper_id
  if (paperId) emit('open-paper', { paper_id: paperId })
}
onMounted(() => Promise.all([loadPapers(), loadEvaluations()]))
watch(() => props.kbId, () => {
  if (evaluationTimer) {
    window.clearInterval(evaluationTimer)
    evaluationTimer = null
  }
  Promise.all([loadPapers(), loadEvaluations()])
})
onBeforeUnmount(() => {
  if (evaluationTimer) window.clearInterval(evaluationTimer)
})
</script>

<style scoped lang="less">
.analysis-evaluation-manager { display: flex; flex-direction: column; gap: 16px; }.manager-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 20px; h3 { margin: 0; color: var(--gray-900); font-size: 18px; } p { margin: 6px 0 0; color: var(--gray-500); } }.form-alert { margin-bottom: 16px; }.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }.report-meta, .preference-row { display: flex; gap: 14px; flex-wrap: wrap; margin: 0 0 16px; color: var(--gray-600); }.blind-reports { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-top: 16px; }.blind-report { padding: 16px; border: 1px solid var(--gray-200); border-radius: 8px; h4 { margin: 0 0 12px; color: var(--gray-900); } }.report-overview { display: flex; flex-direction: column; gap: 12px; margin-bottom: 14px; > div { padding: 10px; background: var(--gray-30); border-radius: 6px; } span { display: block; margin-bottom: 4px; color: var(--gray-500); font-size: 12px; } p, ul { margin: 0; color: var(--gray-700); line-height: 1.6; font-size: 13px; white-space: pre-wrap; } ul { padding-left: 18px; } small { color: var(--gray-500); } }.score-row { display: flex; justify-content: space-between; gap: 12px; align-items: center; padding: 10px 0; border-top: 1px solid var(--gray-100); font-size: 13px; }.review-form { margin-top: 20px; }.disabled { color: var(--gray-400); pointer-events: none; } @media (max-width: 760px) { .manager-header, .form-grid { grid-template-columns: 1fr; flex-direction: column; }.blind-reports { grid-template-columns: 1fr; }.score-row { align-items: flex-start; flex-direction: column; } }
</style>
