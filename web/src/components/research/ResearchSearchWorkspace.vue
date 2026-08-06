<template>
  <section class="search-workspace">
    <div class="search-grid">
      <section class="search-form">
        <div class="search-mode-toolbar">
          <div class="toolbar-field search-mode-field">
            <label>检索模式</label>
            <a-radio-group
              v-model:value="filters.retrievalMode"
              option-type="button"
              button-style="solid"
              @change="clearResult"
            >
              <a-radio-button v-for="mode in searchModeOptions" :key="mode.value" :value="mode.value">
                {{ mode.label }}
              </a-radio-button>
            </a-radio-group>
          </div>
          <span class="search-mode-help">{{ selectedSearchMode.description }}</span>
        </div>

        <a-alert
          v-if="strictModeBlocked"
          type="warning"
          show-icon
          message="引用图谱尚未同步"
          :description="'严格图谱模式要求知识库已同步引用图谱且包含引用关系。请先在「引用图谱」标签页执行同步，或切换为「本地混合」模式。'"
          class="strict-mode-warning"
        />

        <div class="search-options">
          <div class="toolbar-field">
            <label for="search-database">论文知识库</label>
            <a-select
              id="search-database"
              :value="kbId"
              :options="databaseOptions"
              :loading="databasesLoading"
              show-search
              option-filter-prop="label"
              @change="$emit('change-database', $event)"
            />
          </div>
          <div class="toolbar-field">
            <label>发表年份</label>
            <div class="year-range">
              <a-input-number v-model:value="filters.yearFrom" :min="1500" :max="maxPublicationYear" placeholder="起始" />
              <span>至</span>
              <a-input-number v-model:value="filters.yearTo" :min="1500" :max="maxPublicationYear" placeholder="结束" />
            </div>
          </div>
          <div class="toolbar-field compact-option">
            <label for="search-top-k">返回论文数</label>
            <a-input-number id="search-top-k" v-model:value="filters.topK" :min="1" :max="50" />
          </div>
          <div class="toolbar-field compact-option">
            <label for="search-recall-k">候选证据数</label>
            <a-input-number id="search-recall-k" v-model:value="filters.recallTopK" :min="10" :max="200" />
          </div>
        </div>

        <label for="research-query" class="query-label">自然语言研究问题</label>
        <a-textarea
          id="research-query"
          data-testid="research-query"
          v-model:value="searchQuery"
          :rows="4"
          :maxlength="4000"
          show-count
          placeholder="例如：近五年基于图神经网络的药物分子性质预测方法有哪些共同局限？"
          @keydown.ctrl.enter="runResearchSearch"
        />
        <div class="search-form-footer">
          <div class="search-pipeline-tags" aria-label="检索处理阶段">
            <a-tag v-if="filters.retrievalMode === strictHybridGraphMode" color="blue">图谱预检</a-tag>
            <a-tag>查询改写</a-tag>
            <a-tag>向量 + BM25</a-tag>
            <a-tag>Cross-Encoder</a-tag>
            <a-tag v-if="filters.retrievalMode === strictHybridGraphMode" color="blue">引用图谱 PPR</a-tag>
            <a-tag v-else color="green">本地证据聚合</a-tag>
          </div>
          <a-button data-testid="research-search-submit" type="primary" :loading="searchLoading" :disabled="!kbId || !searchQuery.trim()" @click="runResearchSearch">
            <template #icon><Search :size="15" /></template>
            开始检索
          </a-button>
        </div>
      </section>

      <aside class="search-history">
        <header class="history-header">
          <div>
            <h3>检索历史</h3>
            <span>{{ historyTotal }} 条</span>
          </div>
          <a-tooltip title="刷新检索历史">
            <a-button
              type="text"
              class="history-icon-button"
              :loading="historyLoading"
              :disabled="!kbId"
              aria-label="刷新检索历史"
              @click="loadHistory"
            >
              <RefreshCw :size="15" />
            </a-button>
          </a-tooltip>
        </header>

        <a-alert v-if="historyError" type="error" :message="historyError" show-icon class="history-error" />
        <a-skeleton v-if="historyLoading && !historyRuns.length" active :paragraph="{ rows: 5 }" class="history-skeleton" />
        <a-empty v-else-if="!historyRuns.length" description="暂无检索历史" class="history-empty" />
        <div v-else class="history-list">
          <article
            v-for="run in historyRuns"
            :key="run.run_id"
            class="history-item"
            :class="{ active: selectedRunId === run.run_id }"
            role="button"
            tabindex="0"
            @click="selectHistoryRun(run)"
            @keydown.enter="selectHistoryRun(run)"
          >
            <div class="history-item-heading">
              <span class="history-query">{{ run.query || '未命名研究问题' }}</span>
              <div class="history-actions" @click.stop>
                <a-tooltip :title="run.is_pinned ? '取消置顶' : '置顶'">
                  <a-button
                    type="text"
                    class="history-icon-button"
                    :loading="pinningRunId === run.run_id"
                    :aria-label="run.is_pinned ? '取消置顶' : '置顶检索'"
                    @click="togglePinned(run)"
                  >
                    <PinOff v-if="run.is_pinned" :size="14" />
                    <Pin v-else :size="14" />
                  </a-button>
                </a-tooltip>
                <a-popconfirm
                  title="删除这条检索历史？"
                  ok-text="删除"
                  cancel-text="取消"
                  :disabled="run.status === 'running'"
                  @confirm="deleteHistoryRun(run)"
                >
                  <a-tooltip :title="run.status === 'running' ? '运行中的检索不能删除' : '删除'">
                    <a-button
                      type="text"
                      danger
                      class="history-icon-button"
                      :disabled="run.status === 'running'"
                      :loading="deletingRunId === run.run_id"
                      aria-label="删除检索历史"
                    >
                      <Trash2 :size="14" />
                    </a-button>
                  </a-tooltip>
                </a-popconfirm>
              </div>
            </div>
            <div class="history-meta">
              <span class="history-status" :class="`status-${run.status}`">{{ statusLabel(run.status) }}</span>
              <span>{{ getSearchModeLabel(run.config?.retrieval?.mode) }}</span>
              <span>{{ run.result_count || 0 }} 篇</span>
            </div>
            <time>{{ formatDateTime(run.created_at) }}</time>
          </article>
        </div>
        <a-pagination
          v-if="historyTotal > historyPageSize"
          v-model:current="historyPage"
          :page-size="historyPageSize"
          :total="historyTotal"
          :show-size-changer="false"
          size="small"
          class="history-pagination"
          @change="loadHistory"
        />
      </aside>
    </div>

    <a-result v-if="searchError" status="error" title="科研检索失败" :sub-title="searchError">
      <template #extra>
        <a-space wrap>
          <a-button @click="loadHistory">刷新历史</a-button>
          <a-button
            v-if="selectedRun?.retryable"
            type="primary"
            :loading="retryingRunId === selectedRun.run_id"
            @click="retryFailedRun(selectedRun)"
          >
            重试失败运行
          </a-button>
          <a-button v-else type="primary" :disabled="!searchQuery.trim()" @click="runResearchSearch">重新执行</a-button>
        </a-space>
      </template>
    </a-result>

    <a-skeleton v-else-if="detailLoading" active :paragraph="{ rows: 8 }" class="detail-loading" />

    <a-result
      v-else-if="selectedRun && !searchResult"
      :status="selectedRun.status === 'failed' ? 'error' : 'info'"
      :title="selectedRun.status === 'failed' ? '该次检索执行失败' : '该历史运行没有结果快照'"
      :sub-title="selectedRun.error_message || '可以按原研究问题和检索配置重新执行。'"
    >
      <template #extra>
        <a-button type="primary" @click="rerunHistoryRun(selectedRun)">
          <template #icon><RotateCcw :size="15" /></template>
          按原配置重跑
        </a-button>
      </template>
    </a-result>

    <div v-else-if="searchResult" class="search-result-shell">
      <div class="search-result-summary">
        <div>
          <div class="search-result-title-row">
            <h2>{{ searchResult.total }} 篇相关论文</h2>
            <a-tag :color="searchResult.config?.mode === strictHybridGraphMode ? 'blue' : 'green'">
              {{ getSearchModeLabel(searchResult.config?.mode) }}
            </a-tag>
          </div>
          <p>改写查询：{{ searchResult.rewritten_query }}</p>
          <div class="query-keywords">
            <a-tag v-for="keyword in searchResult.keywords" :key="keyword">{{ keyword }}</a-tag>
          </div>
        </div>
        <div class="result-actions">
          <a-button class="lucide-icon-btn" @click="rerunCurrentResult">
            <template #icon><RotateCcw :size="15" /></template>
            按此配置重跑
          </a-button>
          <a-button type="primary" class="lucide-icon-btn" @click="startSynthesis">
            <template #icon><FileText :size="15" /></template>
            生成证据综述
          </a-button>
        </div>
      </div>
      <div class="run-meta">
        <span>运行 {{ searchResult.run_id }}</span>
        <span v-for="(duration, stage) in searchResult.stage_timings" :key="stage">{{ stage }} {{ duration }} ms</span>
      </div>

      <section v-if="searchResult.graph_expansion?.items?.length" class="citation-expansion-panel">
        <div class="search-paper-heading">
          <div>
            <h3>引用图谱扩展</h3>
            <p>
              {{ searchResult.graph_expansion.seed_count }} 个种子论文沿 CITES 网络扩展出
              {{ searchResult.graph_expansion.expanded_count }} 篇关联论文
            </p>
          </div>
          <a-tag color="blue">PPR {{ searchResult.graph_expansion.citation_count }} 条边</a-tag>
        </div>
        <div class="citation-expansion-list">
          <article
            v-for="paper in searchResult.graph_expansion.items"
            :key="paper.graph_paper_id"
            class="citation-expansion-item"
          >
            <div>
              <strong>{{ paper.title || '未命名论文' }}</strong>
              <p>{{ paper.publication_year || '年份未知' }} · {{ paper.venue || '来源未知' }}</p>
            </div>
            <a-tag>图分数 {{ Number(paper.graph_score).toFixed(3) }}</a-tag>
          </article>
        </div>
      </section>

      <div class="search-result-list">
        <article v-for="item in searchResult.items" :key="item.paper_id" class="search-paper-card">
          <div class="search-paper-heading">
            <div>
              <h3>{{ item.title }}</h3>
              <p>{{ item.authors?.join('、') }}<span v-if="item.publication_year"> · {{ item.publication_year }}</span></p>
            </div>
            <span class="ranking-score">{{ Number(item.ranking_score).toFixed(3) }}</span>
          </div>
          <div class="score-row">
            <template v-for="(score, name) in item.scores" :key="name">
              <a-tag v-if="score !== null">{{ name }} {{ Number(score).toFixed(3) }}</a-tag>
            </template>
          </div>
          <div class="evidence-list">
            <div v-for="evidence in item.evidence" :key="evidence.chunk_id" class="evidence-item">
              <div class="evidence-heading">
                <span>{{ evidence.section_title || evidence.section_type || '论文内容' }}</span>
                <span v-if="evidence.source_page_start">页码 {{ evidence.source_page_start }}<template v-if="evidence.source_page_end && evidence.source_page_end !== evidence.source_page_start">–{{ evidence.source_page_end }}</template></span>
                <span v-if="evidence.start_char_pos !== null">字符 {{ evidence.start_char_pos }}–{{ evidence.end_char_pos }}</span>
              </div>
              <p>{{ evidence.content }}</p>
              <a-button
                v-if="evidence.locator"
                type="link"
                size="small"
                class="evidence-source-link"
                @click="$emit('open-evidence', { paper: item, evidence })"
              >
                查看原文定位
              </a-button>
            </div>
          </div>
          <a-button type="link" class="paper-detail-link" @click="$emit('open-paper', item)">查看论文详情</a-button>
        </article>
      </div>
    </div>

    <a-empty v-else description="输入研究问题后开始检索" class="page-empty" />
  </section>
</template>

<script setup>
// ResearchCompass 论文检索工作台
// 本组件是本仓库在开源智能体框架 Yuxi 之上实现的科研业务界面，负责自然语言研究
// 问题的检索执行、检索历史管理（置顶 / 删除 / 重跑）、结果证据查看与一键转入证据
// 综述；支持本地混合与严格图谱两种检索模式。通用表格、抽屉与请求基础设施由 Yuxi
// 提供。
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { FileText, Pin, PinOff, RefreshCw, RotateCcw, Search, Trash2 } from 'lucide-vue-next'
import { researchApi } from '@/apis/research_api'

const props = defineProps({
  kbId: { type: String, default: '' },
  focusRunId: { type: String, default: '' },
  databaseOptions: { type: Array, default: () => [] },
  databasesLoading: { type: Boolean, default: false },
  maxPublicationYear: { type: Number, required: true }
})

const emit = defineEmits([
  'change-database',
  'open-paper',
  'open-evidence',
  'start-synthesis',
  'selection-change'
])

const localHybridMode = 'local_hybrid'
const strictHybridGraphMode = 'strict_hybrid_citation_graph'
const searchModeOptions = [
  {
    value: localHybridMode,
    label: '本地混合',
    description: '不依赖引用图谱，返回可追溯的文本证据'
  },
  {
    value: strictHybridGraphMode,
    label: '严格图谱',
    description: '要求引用图谱可用，并执行 PPR 扩展'
  }
]

const searchQuery = ref('')
const searchLoading = ref(false)
const searchError = ref('')
const searchResult = ref(null)
const historyRuns = ref([])
const historyTotal = ref(0)
const historyLoading = ref(false)
const historyError = ref('')
const historyPage = ref(1)
const historyPageSize = 10
const selectedRunId = ref('')
const selectedRun = ref(null)
const detailLoading = ref(false)
const pinningRunId = ref('')
const deletingRunId = ref('')
const retryingRunId = ref('')
const graphCounts = ref(null)
let requestGeneration = 0

const strictModeBlocked = computed(() => {
  if (filters.retrievalMode !== strictHybridGraphMode) return false
  const counts = graphCounts.value
  if (!counts) return true
  return (counts.papers || 0) <= 0 || (counts.citations || 0) <= 0
})

const loadGraphStatus = async () => {
  if (!props.kbId || filters.retrievalMode !== strictHybridGraphMode) {
    graphCounts.value = null
    return
  }
  try {
    const result = await researchApi.getAcademicGraph(props.kbId, { limit: 1 })
    graphCounts.value = result?.counts || null
  } catch {
    graphCounts.value = null
  }
}

const filters = reactive({
  yearFrom: null,
  yearTo: null,
  topK: 10,
  recallTopK: 50,
  retrievalMode: localHybridMode
})

const selectedSearchMode = computed(() => (
  searchModeOptions.find((mode) => mode.value === filters.retrievalMode) || searchModeOptions[0]
))

const getSearchModeLabel = (mode) => (
  searchModeOptions.find((item) => item.value === mode)?.label || '未知模式'
)

const statusLabel = (status) => ({
  running: '运行中',
  success: '已完成',
  failed: '失败'
})[status] || status || '未知状态'

const formatDateTime = (value) => {
  if (!value) return '时间未知'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString()
}

const clearResult = () => {
  selectedRunId.value = ''
  selectedRun.value = null
  searchResult.value = null
  searchError.value = ''
  emit('selection-change', null)
}

const loadHistory = async () => {
  if (!props.kbId) {
    historyRuns.value = []
    historyTotal.value = 0
    return
  }
  const generation = requestGeneration
  const kbId = props.kbId
  historyLoading.value = true
  historyError.value = ''
  try {
    const result = await researchApi.listSearchRuns(kbId, {
      offset: (historyPage.value - 1) * historyPageSize,
      limit: historyPageSize
    })
    if (generation !== requestGeneration || kbId !== props.kbId) return
    historyRuns.value = result.items || []
    historyTotal.value = result.total || 0
    const maxPage = Math.max(1, Math.ceil(historyTotal.value / historyPageSize))
    if (historyPage.value > maxPage) {
      historyPage.value = maxPage
      await loadHistory()
    }
  } catch (error) {
    if (generation !== requestGeneration) return
    historyError.value = error.message || '检索历史加载失败'
  } finally {
    if (generation === requestGeneration) historyLoading.value = false
  }
}

const applyRunConfig = (run) => {
  const retrieval = run?.config?.retrieval || run?.config || {}
  searchQuery.value = run?.query || run?.raw_query || searchQuery.value
  filters.retrievalMode = retrieval.mode || localHybridMode
  filters.topK = Number(retrieval.top_k || 10)
  filters.recallTopK = Number(retrieval.recall_top_k || 50)
  filters.yearFrom = retrieval.year_from ?? null
  filters.yearTo = retrieval.year_to ?? null
}

const selectHistoryRun = async (run) => {
  if (!run?.run_id || detailLoading.value) return
  const generation = requestGeneration
  const kbId = props.kbId
  selectedRunId.value = run.run_id
  selectedRun.value = run
  searchResult.value = null
  searchError.value = ''
  detailLoading.value = true
  try {
    const detail = await researchApi.getSearchRun(run.run_id)
    if (generation !== requestGeneration || kbId !== props.kbId || selectedRunId.value !== run.run_id) return
    selectedRun.value = detail
    emit('selection-change', detail)
    applyRunConfig(detail)
    searchResult.value = detail.result || null
  } catch (error) {
    if (generation !== requestGeneration) return
    searchError.value = error.message || '检索历史详情加载失败'
  } finally {
    if (generation === requestGeneration) detailLoading.value = false
  }
}

const runResearchSearch = async () => {
  if (!props.kbId || !searchQuery.value.trim() || searchLoading.value) return
  if (filters.yearFrom && filters.yearTo && filters.yearFrom > filters.yearTo) {
    searchError.value = '起始年份不能大于结束年份'
    return
  }
  if (filters.recallTopK < filters.topK) {
    searchError.value = '候选证据数不能小于返回论文数'
    return
  }
  if (strictModeBlocked.value) {
    searchError.value = '当前知识库的引用图谱尚未同步（论文或引用关系为空），请先在"引用图谱"标签页同步图谱，或切换为"本地混合"模式'
    return
  }
  const generation = requestGeneration
  const kbId = props.kbId
  searchLoading.value = true
  searchError.value = ''
  try {
    const result = await researchApi.searchPapers(kbId, {
      query: searchQuery.value.trim(),
      retrieval_mode: filters.retrievalMode,
      top_k: filters.topK,
      recall_top_k: filters.recallTopK,
      year_from: filters.yearFrom,
      year_to: filters.yearTo
    })
    if (generation !== requestGeneration || kbId !== props.kbId) return
    searchResult.value = result
    selectedRunId.value = result.run_id
    selectedRun.value = {
      run_id: result.run_id,
      query: result.query,
      status: 'success',
      result_count: result.total,
      config: { retrieval: result.config },
      result
    }
    emit('selection-change', selectedRun.value)
    historyPage.value = 1
    await loadHistory()
  } catch (error) {
    if (generation !== requestGeneration) return
    searchError.value = error.message || '科研检索失败'
    historyPage.value = 1
    await loadHistory()
  } finally {
    if (generation === requestGeneration) searchLoading.value = false
  }
}

const togglePinned = async (run) => {
  if (!run?.run_id || pinningRunId.value) return
  const generation = requestGeneration
  pinningRunId.value = run.run_id
  historyError.value = ''
  try {
    await researchApi.updateSearchRun(run.run_id, { is_pinned: !run.is_pinned })
    if (generation !== requestGeneration) return
    await loadHistory()
  } catch (error) {
    if (generation !== requestGeneration) return
    historyError.value = error.message || '检索历史置顶失败'
  } finally {
    if (generation === requestGeneration) pinningRunId.value = ''
  }
}

const deleteHistoryRun = async (run) => {
  if (!run?.run_id || run.status === 'running' || deletingRunId.value) return
  const generation = requestGeneration
  deletingRunId.value = run.run_id
  historyError.value = ''
  try {
    await researchApi.deleteSearchRun(run.run_id)
    if (generation !== requestGeneration) return
    if (selectedRunId.value === run.run_id) clearResult()
    await loadHistory()
    message.success('检索历史已删除')
  } catch (error) {
    if (generation !== requestGeneration) return
    historyError.value = error.message || '检索历史删除失败'
  } finally {
    if (generation === requestGeneration) deletingRunId.value = ''
  }
}

const rerunHistoryRun = async (run) => {
  applyRunConfig(run)
  await runResearchSearch()
}

const retryFailedRun = async (run) => {
  if (!run?.run_id || !run.retryable || retryingRunId.value) return
  const generation = requestGeneration
  retryingRunId.value = run.run_id
  searchError.value = ''
  try {
    const result = await researchApi.retrySearchRun(run.run_id)
    if (generation !== requestGeneration) return
    searchQuery.value = result.query || run.query || searchQuery.value
    await loadHistory()
    await selectHistoryRun({ run_id: result.run_id })
    message.success('已提交检索重试任务')
  } catch (error) {
    if (generation !== requestGeneration) return
    searchError.value = error.message || '检索重试失败'
  } finally {
    if (generation === requestGeneration) retryingRunId.value = ''
  }
}

const rerunCurrentResult = async () => {
  applyRunConfig(selectedRun.value || { query: searchResult.value?.query, config: searchResult.value?.config })
  await runResearchSearch()
}

const startSynthesis = () => {
  const retrieval = selectedRun.value?.config?.retrieval || searchResult.value?.config || {}
  emit('start-synthesis', {
    query: searchResult.value?.query || selectedRun.value?.query || searchQuery.value,
    yearFrom: retrieval.year_from ?? null,
    yearTo: retrieval.year_to ?? null,
    topK: retrieval.top_k || filters.topK,
    recallTopK: retrieval.recall_top_k || filters.recallTopK
  })
}

const resetWorkspace = () => {
  requestGeneration += 1
  searchLoading.value = false
  historyLoading.value = false
  detailLoading.value = false
  pinningRunId.value = ''
  deletingRunId.value = ''
  retryingRunId.value = ''
  searchError.value = ''
  historyError.value = ''
  searchResult.value = null
  selectedRun.value = null
  selectedRunId.value = ''
  historyRuns.value = []
  historyTotal.value = 0
  historyPage.value = 1
  graphCounts.value = null
  emit('selection-change', null)
  void loadHistory()
  void loadGraphStatus()
}

watch(() => props.kbId, resetWorkspace)
watch(() => filters.retrievalMode, () => { void loadGraphStatus() })
watch(
  () => [props.focusRunId, props.kbId],
  ([runId, kbId]) => {
    if (runId && kbId) void selectHistoryRun({ run_id: runId })
  },
  { immediate: true }
)
onMounted(loadHistory)
defineExpose({ refresh: loadHistory })
onBeforeUnmount(() => { requestGeneration += 1 })
</script>

<style scoped lang="less">
.search-workspace {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.search-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 340px);
  gap: 14px;
  align-items: start;
}

.search-form,
.search-history {
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.search-form {
  padding: 18px;
}

.search-mode-toolbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 16px;
  padding: 12px 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-10);
}

.toolbar-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;

  label {
    color: var(--color-text-secondary);
    font-size: 12px;
    font-weight: 500;
  }
}

.toolbar-field.search-mode-field {
  align-items: flex-end;
  flex-direction: row;
  gap: 12px;
}

.search-mode-field :deep(.ant-radio-group) {
  display: inline-flex;
}

.search-mode-field label {
  margin: 0;
  white-space: nowrap;
}

.search-mode-help {
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 32px;
  text-align: right;
}

.strict-mode-warning {
  margin-bottom: 12px;
}

.search-form > label {
  display: block;
  margin-bottom: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 500;
}

.search-options {
  display: grid;
  grid-template-columns: minmax(180px, 1.2fr) minmax(220px, 1fr) 110px 110px;
  gap: 12px;
  align-items: end;
  margin-bottom: 16px;
}

.year-range {
  display: flex;
  align-items: center;
  gap: 7px;

  :deep(.ant-input-number) {
    width: 110px;
  }

  span {
    color: var(--color-text-tertiary);
    font-size: 12px;
  }
}

.compact-option :deep(.ant-input-number) {
  width: 100%;
}

.query-label {
  margin-top: 2px;
}

.search-form-footer,
.search-result-summary,
.search-paper-heading,
.evidence-heading,
.run-meta,
.score-row {
  display: flex;
  align-items: center;
}

.search-form-footer {
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.search-pipeline-tags,
.query-keywords,
.result-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.search-history {
  display: flex;
  width: 100%;
  max-height: 420px;
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
}

.history-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 14px 12px;
  border-bottom: 1px solid var(--gray-100);
  background: var(--gray-10);

  h3 {
    margin: 0;
    color: var(--color-text);
    font-size: 14px;
    font-weight: 600;
  }

  span {
    color: var(--color-text-tertiary);
    font-size: 11px;
  }
}

.history-icon-button {
  display: inline-flex;
  width: 32px;
  min-width: 32px;
  height: 32px;
  align-items: center;
  justify-content: center;
  padding: 0;
}

.history-error {
  margin: 10px;
}

.history-skeleton,
.history-empty {
  padding: 18px 14px;
}

.history-list {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
  overflow-y: auto;
}

.history-item {
  padding: 12px 14px;
  border-bottom: 1px solid var(--gray-100);
  background: var(--gray-0);
  cursor: pointer;
  outline: none;

  &:hover {
    background: var(--gray-10);
  }

  &:focus-visible {
    box-shadow: inset 0 0 0 2px var(--main-color);
  }

  &.active {
    background: var(--main-25);
    box-shadow: inset 3px 0 0 var(--main-color);
  }

  time {
    display: block;
    margin-top: 6px;
    color: var(--color-text-tertiary);
    font-size: 11px;
  }
}

.history-item-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.history-query {
  display: -webkit-box;
  min-width: 0;
  overflow: hidden;
  color: var(--color-text);
  font-size: 13px;
  font-weight: 500;
  line-height: 1.45;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.history-actions {
  display: flex;
  flex: none;
  gap: 2px;
  margin: -6px -6px 0 0;
}

.history-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 9px;
  margin-top: 6px;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.history-status {
  font-weight: 600;
}

.status-success { color: var(--color-success-700); }
.status-running { color: var(--color-info-700); }
.status-failed { color: var(--color-error-700); }

.history-pagination {
  padding: 10px 12px;
  border-top: 1px solid var(--gray-100);
}

.detail-loading,
.page-empty {
  padding: 48px 20px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}

.search-result-shell {
  min-width: 0;
}

.search-result-summary {
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--gray-100);

  h2,
  p {
    margin: 0;
  }

  h2 {
    color: var(--color-text);
    font-size: 17px;
  }

  p {
    margin-top: 5px;
    color: var(--color-text-secondary);
    font-size: 12px;
  }
}

.search-result-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.query-keywords {
  margin-top: 8px;
}

.run-meta {
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 5px 10px;
  margin: 8px 0 14px;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.citation-expansion-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 14px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-10);
}

.citation-expansion-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 8px;
}

.citation-expansion-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding: 10px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  background: var(--gray-0);

  strong {
    color: var(--color-text);
    font-size: 13px;
  }

  p {
    margin: 4px 0 0;
    color: var(--color-text-secondary);
    font-size: 11px;
  }
}

.search-result-list,
.evidence-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.search-paper-card {
  padding: 15px;
  border: 1px solid var(--gray-150);
  border-radius: 7px;
  background: var(--gray-10);
}

.search-paper-heading {
  justify-content: space-between;
  gap: 12px;

  h3,
  p {
    margin: 0;
  }

  h3 {
    color: var(--color-text);
    font-size: 15px;
  }

  p {
    margin-top: 4px;
    color: var(--color-text-secondary);
    font-size: 12px;
  }
}

.ranking-score {
  flex: none;
  color: var(--main-color);
  font-size: 14px;
  font-variant-numeric: tabular-nums;
  font-weight: 650;
}

.score-row {
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 10px;
}

.evidence-list {
  margin-top: 12px;
}

.evidence-item {
  padding: 10px;
  border-left: 3px solid var(--main-200);
  background: var(--gray-0);

  p {
    display: -webkit-box;
    margin: 5px 0 0;
    overflow: hidden;
    color: var(--color-text-secondary);
    font-size: 12px;
    line-height: 1.55;
    -webkit-line-clamp: 5;
    -webkit-box-orient: vertical;
  }
}

.evidence-heading {
  justify-content: space-between;
  gap: 8px;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.evidence-source-link,
.paper-detail-link {
  padding: 2px 0;
  margin-top: 5px;
  font-size: 12px;
}

@media (max-width: 1100px) {
  .search-grid {
    grid-template-columns: 1fr;
  }

  .search-history {
    max-height: 420px;
  }

  .search-options {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 700px) {
  .search-form {
    padding: 14px;
  }

  .search-options {
    grid-template-columns: 1fr;
  }

  .search-mode-toolbar,
  .toolbar-field.search-mode-field,
  .search-form-footer,
  .search-result-summary {
    align-items: stretch;
    flex-direction: column;
  }

  .search-mode-field :deep(.ant-radio-group) {
    width: 100%;
  }

  .search-mode-field :deep(.ant-radio-button-wrapper) {
    flex: 1;
    min-width: 0;
    padding: 0 4px;
    font-size: 12px;
    text-align: center;
  }

  .search-mode-help {
    line-height: 1.5;
    text-align: left;
  }

  .year-range :deep(.ant-input-number) {
    width: calc(50% - 14px);
  }

  .search-form-footer :deep(.ant-btn),
  .result-actions :deep(.ant-btn) {
    width: 100%;
  }

  .result-actions,
  .search-result-title-row,
  .search-paper-heading,
  .evidence-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .run-meta {
    justify-content: flex-start;
  }
}
</style>
