<template>
  <div class="experiment-tab">
    <div class="experiment-header">
      <div>
        <h3>消融实验</h3>
        <p>同一评估基准、共享模型条件下比较多组检索配置；系统只报告实测差异。</p>
      </div>
      <a-space class="experiment-actions">
        <a-button class="lucide-icon-btn" :loading="manifestDownloading" @click="downloadCorpusManifest">
          <FileDown :size="16" />
          固定语料清单
        </a-button>
        <a-button type="primary" class="lucide-icon-btn" :disabled="datasets.length === 0" @click="openCreate">
          <FlaskConical :size="16" />
          新建实验
        </a-button>
      </a-space>
    </div>

    <ResourceEmptyState
      v-if="!loading && experiments.length === 0"
      title="暂无消融实验"
      :description="datasets.length ? '选择同一基准，配置基线和对照组后开始实测。' : '请先在评估基准中上传或生成可用数据集。'"
      :icon="FlaskConical"
    />

    <a-table
      v-else
      :loading="loading"
      :data-source="experiments"
      :columns="columns"
      row-key="experiment_id"
      :pagination="false"
      size="middle"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'status'">
          <a-tag :color="statusColor(record.status)">{{ statusText(record.status) }}</a-tag>
        </template>
        <template v-else-if="column.key === 'progress'">
          {{ record.completed_variants }}/{{ record.total_variants }}
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-space>
            <a @click="viewExperiment(record)">报告</a>
            <a-popconfirm
              :title="record.status === 'running' || record.status === 'queued' ? '运行中的实验不能删除' : '删除实验及其对比报告？'"
              :disabled="record.status === 'running' || record.status === 'queued'"
              @confirm="deleteExperiment(record)"
            >
              <a :class="{ disabled: record.status === 'running' || record.status === 'queued' }">删除</a>
            </a-popconfirm>
          </a-space>
        </template>
      </template>
    </a-table>

    <a-modal v-model:open="createVisible" title="新建消融实验" :confirm-loading="creating" width="760px" @ok="createExperiment">
      <a-alert
        show-icon
        type="info"
        message="公平对比"
        description="所有变体固定使用同一个评估基准和答案/Judge 模型。跨知识库比较时，系统会校验已索引文档内容哈希集合和嵌入模型完全一致，并按源文档计算检索指标。"
        class="experiment-alert"
      />
      <a-form layout="vertical" :model="form">
        <div class="form-grid">
          <a-form-item label="实验名称" required>
            <a-input v-model:value="form.name" :maxlength="100" placeholder="例如：向量检索与图谱检索对比" />
          </a-form-item>
          <a-form-item label="评估基准" required>
            <a-select v-model:value="form.dataset_id" placeholder="选择已完成的评估基准">
              <a-select-option v-for="dataset in datasets" :key="dataset.dataset_id" :value="dataset.dataset_id">
                {{ dataset.name }}（{{ dataset.item_count }} 题）
              </a-select-option>
            </a-select>
            <a-button
              v-if="form.dataset_id"
              type="link"
              size="small"
              class="template-download"
              :loading="templateDownloading"
              @click="downloadBaselineTemplate"
            >
              <FileDown :size="14" /> 下载外部基线模板
            </a-button>
          </a-form-item>
        </div>
        <a-form-item label="实验说明">
          <a-textarea v-model:value="form.description" :maxlength="2000" :rows="2" placeholder="说明唯一变化的因素和预期验证的问题" />
        </a-form-item>
        <div class="form-grid">
          <a-form-item label="答案生成模型（可选）">
            <ModelSelectorComponent v-model:model_spec="form.answer_llm" size="small" displayName="mini" />
          </a-form-item>
          <a-form-item label="答案评判模型（可选）">
            <ModelSelectorComponent v-model:model_spec="form.judge_llm" size="small" displayName="mini" />
          </a-form-item>
        </div>
        <div class="variant-heading">
          <strong>实验变体</strong><span>选择一个基线，其余变体将显示相对于基线的绝对指标差值。</span>
          <a-button type="link" size="small" class="preset-button" @click="loadStandardAblationPreset">
            <ListChecks :size="14" /> 加载标准四组
          </a-button>
        </div>
        <div v-for="(variant, index) in form.variants" :key="index" class="variant-card">
          <div class="variant-card-head">
            <a-radio :checked="variant.baseline" @change="setBaseline(index)">基线</a-radio>
            <a-button v-if="form.variants.length > 2" type="text" danger size="small" @click="removeVariant(index)">
              <Trash2 :size="15" /> 移除
            </a-button>
          </div>
          <a-input v-model:value="variant.name" :maxlength="100" placeholder="变体名称" />
          <a-form-item label="运行方式" class="variant-type-field">
            <a-select v-model:value="variant.execution_type" @change="changeExecutionType(variant)">
              <a-select-option value="research_compass">ResearchCompass 检索</a-select-option>
              <a-select-option value="external_rag">外部通用 RAG 结果</a-select-option>
              <a-select-option value="direct_llm">直接 LLM 结果</a-select-option>
            </a-select>
          </a-form-item>
          <div class="form-grid compact-grid">
            <a-form-item label="对照知识库">
              <a-select v-model:value="variant.kb_id" :options="databaseOptions" :disabled="variant.execution_type !== 'research_compass'" />
            </a-form-item>
            <template v-if="variant.execution_type === 'research_compass'">
              <a-form-item label="检索模式">
                <a-select v-model:value="variant.search_mode">
                  <a-select-option value="vector">纯向量</a-select-option>
                  <a-select-option value="keyword">BM25</a-select-option>
                  <a-select-option value="hybrid">向量 + BM25</a-select-option>
                </a-select>
              </a-form-item>
              <a-form-item label="最终 Top-K">
                <a-input-number v-model:value="variant.final_top_k" :min="1" :max="100" style="width: 100%" />
              </a-form-item>
              <a-form-item label="启用图检索">
                <a-switch v-model:checked="variant.use_graph_retrieval" checked-children="图谱" un-checked-children="关闭" />
              </a-form-item>
              <a-form-item label="图检索权重">
                <a-input-number v-model:value="variant.graph_weight" :min="0" :max="5" :step="0.1" :disabled="!variant.use_graph_retrieval" style="width: 100%" />
              </a-form-item>
            </template>
          </div>
          <div v-if="variant.execution_type !== 'research_compass'" class="external-baseline-fields">
            <div class="external-baseline-upload">
              <a-upload :show-upload-list="false" accept=".jsonl,.json" :before-upload="(file) => loadExternalResults(file, variant)">
                <a-button :loading="variant.external_loading">
                  <FileUp :size="14" /> 导入逐题结果 JSONL
                </a-button>
              </a-upload>
              <span v-if="variant.external_file_name">{{ variant.external_file_name }} · {{ variant.external_results.length }} 题</span>
              <span v-else>请使用上方模板填写同一数据集的逐题结果</span>
            </div>
            <div class="form-grid">
              <a-form-item label="来源系统" required>
                <a-input v-model:value="variant.provenance.system_name" :maxlength="120" placeholder="例如：LlamaIndex 基线" />
              </a-form-item>
              <a-form-item label="来源版本">
                <a-input v-model:value="variant.provenance.system_version" :maxlength="120" placeholder="例如：0.10.0" />
              </a-form-item>
            </div>
            <a-form-item label="来源说明">
              <a-input v-model:value="variant.provenance.notes" :maxlength="2000" placeholder="记录检索器、提示词、模型和运行条件" />
            </a-form-item>
          </div>
        </div>
        <a-button v-if="form.variants.length < 8" block @click="addVariant"><Plus :size="16" /> 添加变体</a-button>
      </a-form>
    </a-modal>

    <a-modal v-model:open="reportVisible" :title="`实验报告 · ${selectedExperiment?.name || ''}`" width="960px" :footer="null">
      <a-spin :spinning="reportLoading">
        <template v-if="selectedExperiment">
          <a-alert
            v-if="selectedExperiment.error_message"
            type="error"
            show-icon
            :message="selectedExperiment.error_message"
            class="experiment-alert"
          />
          <div class="report-meta">
            <span>数据集指纹：<code>{{ selectedExperiment.dataset_fingerprint }}</code></span>
            <span v-if="selectedExperiment.corpus_snapshot?.fingerprint">语料指纹：<code>{{ selectedExperiment.corpus_snapshot.fingerprint }}</code></span>
            <span v-if="selectedExperiment.corpus_snapshot?.document_count">受控文档：{{ selectedExperiment.corpus_snapshot.document_count }} 篇</span>
            <a-tag v-if="selectedExperiment.corpus_snapshot?.retrieval_identity === 'content_hash'" color="blue">按源文档评估</a-tag>
            <span>进度：{{ selectedExperiment.completed_variants }}/{{ selectedExperiment.total_variants }}</span>
            <a-tag :color="statusColor(selectedExperiment.status)">{{ statusText(selectedExperiment.status) }}</a-tag>
          </div>
          <a-table :data-source="selectedExperiment.variants" :columns="reportColumns" row-key="variant_id" :pagination="false" size="small">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'status'">
                <a-tag :color="statusColor(record.status)">{{ statusText(record.status) }}</a-tag>
              </template>
              <template v-else-if="column.key === 'execution'">{{ executionTypeText(record.execution_type) }}</template>
              <template v-else-if="column.key === 'provenance'">{{ record.provenance?.system_name || 'ResearchCompass' }}</template>
              <template v-else-if="column.key === 'recall'">{{ formatMetric(record.metrics?.['recall@10']) }}</template>
              <template v-else-if="column.key === 'f1'">{{ formatMetric(record.metrics?.['f1@10']) }}</template>
              <template v-else-if="column.key === 'answer'">{{ formatMetric(record.metrics?.answer_correctness) }}</template>
              <template v-else-if="column.key === 'overall'">{{ formatMetric(record.metrics?.overall_score) }}</template>
              <template v-else-if="column.key === 'delta'">{{ formatDelta(record.metric_deltas?.['recall@10']) }}</template>
              <template v-else-if="column.key === 'config'">
                <code>{{ record.execution_type === 'research_compass' ? `${kbSummary(record.kb_id)} · ${configSummary(record.retrieval_config)}` : '逐题外部结果，服务端重算指标' }}</code>
              </template>
            </template>
          </a-table>
        </template>
      </a-spin>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { FileDown, FileUp, FlaskConical, ListChecks, Plus, Trash2 } from 'lucide-vue-next'
import { databaseApi, evaluationApi } from '@/apis/knowledge_api'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'
import ResourceEmptyState from '@/components/shared/ResourceEmptyState.vue'

const props = defineProps({ kbId: { type: String, required: true } })

const loading = ref(false)
const datasets = ref([])
const databases = ref([])
const experiments = ref([])
const createVisible = ref(false)
const creating = ref(false)
const templateDownloading = ref(false)
const manifestDownloading = ref(false)
const reportVisible = ref(false)
const reportLoading = ref(false)
const selectedExperiment = ref(null)
let refreshTimer = null

const freshVariant = (name, baseline = false, overrides = {}) => ({
  name,
  baseline,
  kb_id: props.kbId,
  search_mode: 'vector',
  final_top_k: 10,
  use_graph_retrieval: false,
  graph_weight: 1,
  execution_type: 'research_compass',
  provenance: {
    system_name: '',
    system_version: '',
    run_id: '',
    notes: '',
    configuration: {}
  },
  dataset_fingerprint: '',
  corpus_fingerprint: '',
  external_results: [],
  external_file_name: '',
  external_loading: false,
  ...overrides
})

const form = reactive({
  name: '',
  description: '',
  dataset_id: undefined,
  answer_llm: '',
  judge_llm: '',
  variants: [freshVariant('基线：纯向量检索', true), freshVariant('对照：向量 + 图谱')]
})

const columns = [
  { title: '实验', dataIndex: 'name', key: 'name', ellipsis: true },
  { title: '状态', key: 'status', width: 120 },
  { title: '完成变体', key: 'progress', width: 110 },
  { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 185 },
  { title: '操作', key: 'actions', width: 120 }
]

const reportColumns = [
  { title: '变体', dataIndex: 'name', key: 'name', width: 175 },
  { title: '状态', key: 'status', width: 110 },
  { title: '类型', key: 'execution', width: 125 },
  { title: '来源', key: 'provenance', width: 140 },
  { title: 'Recall@10', key: 'recall', width: 105 },
  { title: 'F1@10', key: 'f1', width: 95 },
  { title: '答案准确率', key: 'answer', width: 110 },
  { title: '综合评分', key: 'overall', width: 105 },
  { title: '相对基线 ΔRecall@10', key: 'delta', width: 145 },
  { title: '检索配置', key: 'config' }
]

const hasRunningExperiment = computed(() =>
  experiments.value.some((item) => ['queued', 'running'].includes(item.status))
)
const databaseOptions = computed(() => databases.value.map((database) => ({
  value: database.kb_id,
  label: database.kb_id === props.kbId ? `${database.name}（当前知识库）` : database.name
})))

const statusText = (status) => ({
  queued: '等待中', running: '运行中', completed: '已完成', completed_with_failures: '部分失败', failed: '失败'
}[status] || status)
const statusColor = (status) => ({
  queued: 'default', running: 'processing', completed: 'success', completed_with_failures: 'warning', failed: 'error'
}[status] || 'default')
const executionTypeText = (type) => ({
  research_compass: 'ResearchCompass',
  external_rag: '外部通用 RAG',
  direct_llm: '直接 LLM'
}[type] || type || '未知')
const formatMetric = (value) => Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '-'
const formatDelta = (value) => Number.isFinite(value) ? `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}%` : '-'
const configSummary = (config = {}) => [
  config.search_mode || 'vector',
  `TopK=${config.final_top_k || 10}`,
  config.use_graph_retrieval ? `graph=${config.graph_weight ?? 1}` : 'graph=off'
].join(' · ')
const kbSummary = (kbId) => databases.value.find((database) => database.kb_id === kbId)?.name || kbId || props.kbId

const loadDatabases = async () => {
  const response = await databaseApi.getDatabases()
  databases.value = (response.databases || []).filter((database) => (database.kb_type || 'milvus') === 'milvus')
}

const loadDatasets = async () => {
  const response = await evaluationApi.listDatasets(props.kbId)
  datasets.value = (response.data || []).filter((item) => (item.build_metadata?.status || 'completed') === 'completed')
}

const loadExperiments = async (silent = false) => {
  try {
    loading.value = !silent
    const response = await evaluationApi.listExperiments(props.kbId)
    experiments.value = response.data || []
    if (selectedExperiment.value) {
      selectedExperiment.value = experiments.value.find((item) => item.experiment_id === selectedExperiment.value.experiment_id) || selectedExperiment.value
    }
  } catch (error) {
    console.error('加载消融实验失败:', error)
    if (!silent) message.error('加载消融实验失败')
  } finally {
    loading.value = false
    syncRefresh()
  }
}

const syncRefresh = () => {
  if (hasRunningExperiment.value && !refreshTimer) refreshTimer = window.setInterval(() => loadExperiments(true), 2500)
  if (!hasRunningExperiment.value && refreshTimer) {
    window.clearInterval(refreshTimer)
    refreshTimer = null
  }
}

const resetForm = () => {
  form.name = ''
  form.description = ''
  form.dataset_id = datasets.value[0]?.dataset_id
  form.answer_llm = ''
  form.judge_llm = ''
  form.variants = [freshVariant('基线：纯向量检索', true), freshVariant('对照：向量 + 图谱')]
}

const loadStandardAblationPreset = () => {
  if (!form.name.trim()) form.name = 'ResearchCompass 标准检索消融'
  if (!form.description.trim()) form.description = '固定语料、评估基准和模型，仅比较检索策略。'
  form.variants = [
    freshVariant('基线：纯向量检索', true),
    freshVariant('对照：BM25', false, { search_mode: 'keyword' }),
    freshVariant('对照：向量 + BM25', false, { search_mode: 'hybrid' }),
    freshVariant('对照：混合检索 + 知识图谱增强', false, {
      search_mode: 'hybrid',
      use_graph_retrieval: true
    })
  ]
}

const downloadCorpusManifest = async () => {
  if (manifestDownloading.value) return
  manifestDownloading.value = true
  try {
    const response = await evaluationApi.downloadCorpusManifest(props.kbId)
    const blob = await response.blob()
    const contentDisposition = response.headers.get('content-disposition') || ''
    const encodedFilename = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
    const filename = encodedFilename
      ? decodeURIComponent(encodedFilename)
      : 'researchcompass-corpus-manifest.json'
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    anchor.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    message.error(error.message || '固定语料清单下载失败')
  } finally {
    manifestDownloading.value = false
  }
}

const changeExecutionType = (variant) => {
  if (variant.execution_type === 'research_compass') {
    variant.external_results = []
    variant.external_file_name = ''
    variant.dataset_fingerprint = ''
    variant.corpus_fingerprint = ''
  }
}

const loadExternalResults = async (upload, variant) => {
  variant.external_loading = true
  try {
    const file = upload?.originFileObj || upload
    const lines = (await file.text())
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
    const records = lines.map((line, index) => {
      try {
        return JSON.parse(line)
      } catch {
        throw new Error(`第 ${index + 1} 行不是有效 JSON`)
      }
    })
    const manifest = records.find((record) => record.record_type === 'manifest')
    const results = records.filter((record) => record.record_type === 'result')
    if (!manifest?.dataset_fingerprint || !manifest.corpus_fingerprint || !results.length) {
      throw new Error('文件缺少 manifest 指纹或逐题 result 记录')
    }
    variant.dataset_fingerprint = manifest.dataset_fingerprint
    variant.corpus_fingerprint = manifest.corpus_fingerprint
    variant.external_results = results.map((record) => ({
      item_id: record.item_id,
      query: record.query,
      generated_answer: record.generated_answer || '',
      retrieved_document_hashes: record.retrieved_document_hashes || []
    }))
    variant.external_file_name = file.name || 'external-baseline.jsonl'
    message.success(`已读取 ${variant.external_results.length} 条外部基线结果`)
  } catch (error) {
    variant.external_results = []
    variant.external_file_name = ''
    message.error(error.message || '外部基线文件解析失败')
  } finally {
    variant.external_loading = false
  }
  return false
}

const downloadBaselineTemplate = async () => {
  if (!form.dataset_id || templateDownloading.value) return
  templateDownloading.value = true
  try {
    const response = await evaluationApi.downloadExternalBaselineTemplate(props.kbId, form.dataset_id)
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'external-baseline-template.jsonl'
    anchor.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    message.error(error.message || '外部基线模板下载失败')
  } finally {
    templateDownloading.value = false
  }
}

const openCreate = () => {
  resetForm()
  createVisible.value = true
}

const addVariant = () => form.variants.push(freshVariant(`对照组 ${form.variants.length}`))
const removeVariant = (index) => form.variants.splice(index, 1)
const setBaseline = (selectedIndex) => {
  form.variants.forEach((variant, index) => {
    variant.baseline = index === selectedIndex
  })
}

const createExperiment = async () => {
  if (!form.name.trim() || !form.dataset_id) return message.warning('请填写实验名称并选择评估基准')
  if (Boolean(form.answer_llm) !== Boolean(form.judge_llm)) return message.warning('答案生成模型和评判模型必须同时配置或同时留空')
  const baselineIndex = form.variants.findIndex((variant) => variant.baseline)
  if (baselineIndex < 0 || form.variants.some((variant) => !variant.name.trim())) return message.warning('请指定基线并填写全部变体名称')
  const externalVariantMissing = form.variants.some((variant) => (
    variant.execution_type !== 'research_compass'
    && (!variant.external_results.length || !variant.provenance.system_name.trim())
  ))
  if (externalVariantMissing) return message.warning('外部基线必须导入逐题结果并填写来源系统')
  if (form.variants.some((variant) => variant.execution_type === 'direct_llm') && !form.judge_llm) {
    return message.warning('直接 LLM 基线需要配置答案评判模型')
  }
  creating.value = true
  try {
    const response = await evaluationApi.createExperiment(props.kbId, {
      dataset_id: form.dataset_id,
      name: form.name.trim(),
      description: form.description.trim(),
      baseline_variant_index: baselineIndex,
      shared_config: { answer_llm: form.answer_llm, judge_llm: form.judge_llm },
      variants: form.variants.map((variant) => ({
        name: variant.name.trim(),
        kb_id: variant.kb_id,
        execution_type: variant.execution_type,
        provenance: variant.execution_type === 'research_compass' ? null : variant.provenance,
        dataset_fingerprint: variant.dataset_fingerprint || null,
        corpus_fingerprint: variant.corpus_fingerprint || null,
        external_results: variant.external_results,
        retrieval_config: variant.execution_type === 'research_compass'
          ? {
              search_mode: variant.search_mode,
              final_top_k: variant.final_top_k,
              use_graph_retrieval: variant.use_graph_retrieval,
              ...(variant.use_graph_retrieval ? { graph_weight: variant.graph_weight } : {})
            }
          : {}
      }))
    })
    if (response.message === 'success') {
      message.success('消融实验已开始')
      createVisible.value = false
      await loadExperiments()
    }
  } catch (error) {
    console.error('创建消融实验失败:', error)
    message.error('创建消融实验失败')
  } finally {
    creating.value = false
  }
}

const viewExperiment = async (record) => {
  reportVisible.value = true
  reportLoading.value = true
  try {
    const response = await evaluationApi.getExperiment(props.kbId, record.experiment_id)
    selectedExperiment.value = response.data || null
  } catch (error) {
    console.error('加载实验报告失败:', error)
    message.error('加载实验报告失败')
  } finally {
    reportLoading.value = false
  }
}

const deleteExperiment = async (record) => {
  try {
    await evaluationApi.deleteExperiment(props.kbId, record.experiment_id)
    message.success('实验已删除')
    await loadExperiments()
  } catch (error) {
    console.error('删除实验失败:', error)
    message.error('删除实验失败')
  }
}

onMounted(async () => {
  await Promise.all([loadDatasets(), loadDatabases(), loadExperiments()])
})
onUnmounted(() => refreshTimer && window.clearInterval(refreshTimer))
</script>

<style lang="less" scoped>
.experiment-tab { display: flex; flex-direction: column; gap: 18px; }
.experiment-header { display: flex; justify-content: space-between; gap: 24px; align-items: flex-start;
  h3 { margin: 0; color: var(--gray-900); font-size: 18px; }
  p { margin: 6px 0 0; color: var(--gray-500); }
}
.experiment-alert { margin-bottom: 16px; }
.template-download { display: inline-flex; align-items: center; gap: 5px; padding-left: 0; }
.preset-button { display: inline-flex; align-items: center; gap: 5px; margin-left: auto; padding-right: 0; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
.variant-heading { margin: 4px 0 10px; display: flex; gap: 8px; flex-wrap: wrap; align-items: baseline; color: var(--gray-500); font-size: 13px;
  strong { color: var(--gray-900); font-size: 14px; }
}
.variant-card { margin-bottom: 12px; padding: 14px; border: 1px solid var(--gray-200); background: var(--gray-0); border-radius: 8px; }
.variant-card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.variant-type-field { margin-top: 12px; margin-bottom: 0; }
.compact-grid { margin-top: 12px; }
.external-baseline-fields { margin-top: 12px; padding: 12px; border: 1px solid var(--gray-150); border-radius: 6px; background: var(--gray-25); }
.external-baseline-upload { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 12px; color: var(--color-text-tertiary); font-size: 12px; }
.report-meta { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; color: var(--gray-600); margin: 0 0 16px;
  code { font-size: 11px; }
}
.disabled { color: var(--gray-400); pointer-events: none; }
@media (max-width: 760px) {
  .experiment-header { flex-direction: column; gap: 12px; }
  .experiment-actions { width: 100%; flex-wrap: wrap; }
  .form-grid { grid-template-columns: 1fr; }
}
</style>
