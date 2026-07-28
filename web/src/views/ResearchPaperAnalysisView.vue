<template>
  <div class="analysis-view layout-container">
    <PageHeader title="论文分析报告" :loading="loading">
      <template #info>
        <span class="header-summary">四阶段科研分析流水线</span>
      </template>
      <template #actions>
        <a-button class="lucide-icon-btn" @click="goBack">返回论文库</a-button>
        <a-button type="primary" :loading="starting" :disabled="running" @click="startAnalysis">
          {{ running ? `分析中：${run?.stage || '准备中'}` : '重新生成报告' }}
        </a-button>
      </template>
    </PageHeader>

    <main class="analysis-content">
      <a-result v-if="error" status="error" title="论文分析报告加载失败" :sub-title="error">
        <template #extra>
          <a-button @click="loadReport">重新加载</a-button>
          <a-button @click="goBack">返回论文库</a-button>
        </template>
      </a-result>

      <template v-else>
        <section v-if="paper" class="paper-heading-card">
          <div>
            <p class="eyebrow">RESEARCHCOMPASS / PAPER ANALYSIS</p>
            <h1>{{ paper.title }}</h1>
            <p v-if="paper.authors?.length" class="authors">{{ paper.authors.join('、') }}</p>
            <div class="paper-meta">
              <span v-if="paper.publication_year">发表年份：{{ paper.publication_year }}</span>
              <span v-if="paper.venue">来源：{{ paper.venue }}</span>
              <span v-if="paper.doi">DOI：{{ paper.doi }}</span>
            </div>
          </div>
          <a-tag :color="statusColor">{{ statusText }}</a-tag>
        </section>

        <a-alert
          v-if="running"
          type="info"
          show-icon
          :message="`正在执行 ${run?.stage || '论文分析'} 阶段`"
          description="阶段结果会实时保存；离开页面后可以从最新运行记录继续查看。"
          class="run-alert"
        />

        <a-empty v-if="!loading && !run?.result && !running" description="尚未生成论文分析报告">
          <a-button type="primary" :loading="starting" @click="startAnalysis">生成分析报告</a-button>
        </a-empty>

        <div v-if="run?.result" class="report-grid">
          <section class="report-card report-card-wide">
            <header><span class="stage-index">01</span><div><h2>结构化提取</h2><p>研究问题、方法、数据集、结果与限制</p></div></header>
            <div class="report-body structure-body">
              <div><label>研究问题</label><p>{{ run.result.structure?.problem || '未明确提供' }}</p></div>
              <div><label>方法</label><p>{{ run.result.structure?.method || '未明确提供' }}</p></div>
              <div><label>实验结果</label><p>{{ formatList(run.result.structure?.results) }}</p></div>
              <div><label>局限性</label><p>{{ formatList(run.result.structure?.limitations) }}</p></div>
            </div>
          </section>

          <section class="report-card">
            <header><span class="stage-index">02</span><div><h2>核心创新点</h2><p>仅保留论文证据支持的主张</p></div></header>
            <div class="report-body">
              <article v-for="(item, index) in run.result.innovations?.items || []" :key="`${item.claim}-${index}`" class="innovation-item">
                <strong>{{ item.claim }}</strong>
                <p v-if="item.evidence">证据：{{ item.evidence }}</p>
                <a-tag v-if="item.confidence" :color="confidenceColor(item.confidence)">{{ item.confidence }}</a-tag>
              </article>
              <a-empty v-if="!run.result.innovations?.items?.length" description="未提取到明确创新点" />
            </div>
          </section>

          <section class="report-card">
            <header><span class="stage-index">03</span><div><h2>方法论与复现性</h2><p>研究设计、步骤、评估和复现条件</p></div></header>
            <div class="report-body">
              <div class="method-block"><label>研究设计</label><p>{{ run.result.methodology?.research_design || '未明确提供' }}</p></div>
              <div class="method-block"><label>方法步骤</label><p>{{ formatList(run.result.methodology?.method_steps) }}</p></div>
              <div class="method-block"><label>评估方式</label><p>{{ formatList(run.result.methodology?.evaluation) }}</p></div>
              <a-alert
                :type="run.result.methodology?.reproducibility?.available ? 'success' : 'warning'"
                show-icon
                :message="run.result.methodology?.reproducibility?.available ? '论文提供了复现信息' : '论文未明确提供完整复现信息'"
                :description="formatList(run.result.methodology?.reproducibility?.details)"
              />
            </div>
          </section>

          <section class="report-card report-card-wide">
            <header><span class="stage-index">04</span><div><h2>研究空白与后续方向</h2><p>结合论文证据输出可追溯的研究机会</p></div></header>
            <div class="report-body">
              <div v-for="(item, index) in run.result.research_gaps?.gaps || []" :key="`${item.claim}-${index}`" class="gap-item">
                <strong>{{ item.claim }}</strong>
                <p v-if="item.basis">依据：{{ item.basis }}</p>
                <a-tag v-if="item.confidence" :color="confidenceColor(item.confidence)">{{ item.confidence }}</a-tag>
              </div>
              <div class="future-direction-block">
                <label>后续方向</label>
                <p>{{ formatList(run.result.research_gaps?.future_directions) }}</p>
              </div>
              <a-empty v-if="!run.result.research_gaps?.gaps?.length" description="未提取到明确研究空白" />
            </div>
          </section>
        </div>
      </template>
    </main>
  </div>
</template>

<script setup>
// ResearchCompass 论文分析报告页
// 本视图是本仓库在开源智能体框架 Yuxi 之上实现的科研业务界面，负责展示单篇论文的
// 四阶段分析报告（结构化提取、核心创新点、方法论与复现性、研究空白），并通过轮询
// 跟踪分析任务执行状态；分析流水线后端由本仓库实现，通用智能体运行时由 Yuxi 提供。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import PageHeader from '@/components/shared/PageHeader.vue'
import { researchApi } from '@/apis/research_api'

const route = useRoute()
const router = useRouter()
const kbId = String(route.params.kbId || '')
const paperId = String(route.params.paperId || '')
const paper = ref(null)
const run = ref(null)
const loading = ref(false)
const starting = ref(false)
const error = ref('')
let pollTimer = null

const running = computed(() => ['pending', 'running'].includes(run.value?.status))
const statusText = computed(() => ({
  pending: '排队中',
  running: '分析中',
  success: '分析完成',
  failed: '分析失败'
}[run.value?.status] || '未生成'))
const statusColor = computed(() => ({ pending: 'blue', running: 'processing', success: 'green', failed: 'red' }[run.value?.status] || 'default'))

const formatList = (value) => {
  if (Array.isArray(value)) return value.length ? value.join('；') : '未明确提供'
  if (value && typeof value === 'object') return Object.entries(value).map(([key, item]) => `${key}：${item}`).join('；')
  return String(value || '未明确提供')
}

const confidenceColor = (value) => ({ high: 'green', medium: 'orange', low: 'default' }[value] || 'default')

const clearPolling = () => {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
}

const pollRun = async (runId) => {
  clearPolling()
  try {
    run.value = await researchApi.getPaperAnalysisRun(runId)
    if (running.value) pollTimer = setTimeout(() => pollRun(runId), 2000)
  } catch (err) {
    error.value = err.message || '论文分析状态查询失败'
  }
}

const loadReport = async () => {
  if (!kbId || !paperId) {
    error.value = '缺少论文或知识库标识'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const [detail, latest] = await Promise.all([
      researchApi.getPaper(kbId, paperId),
      researchApi.getLatestPaperAnalysis(kbId, paperId)
    ])
    paper.value = detail.paper
    run.value = latest
    if (latest?.run_id && running.value) await pollRun(latest.run_id)
  } catch (err) {
    error.value = err.message || '论文分析报告加载失败'
  } finally {
    loading.value = false
  }
}

const startAnalysis = async () => {
  if (starting.value || running.value) return
  starting.value = true
  error.value = ''
  try {
    const result = await researchApi.analyzePaper(kbId, paperId)
    run.value = result
    await pollRun(result.run_id)
    message.success('论文分析任务已提交')
  } catch (err) {
    error.value = err.message || '论文分析任务提交失败'
  } finally {
    starting.value = false
  }
}

const goBack = () => router.push({ name: 'ResearchCompass' })

onMounted(loadReport)
onBeforeUnmount(clearPolling)
</script>

<style scoped lang="less">
.analysis-content { display: flex; flex-direction: column; gap: 16px; padding-bottom: 32px; }
.paper-heading-card, .report-card { padding: 20px; border: 1px solid var(--gray-150); border-radius: 12px; background: var(--gray-0); }
.paper-heading-card { display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; }
.eyebrow { margin: 0 0 8px; color: var(--main-color); font-size: 11px; letter-spacing: .08em; }
h1 { margin: 0; color: var(--color-text); font-size: 25px; line-height: 1.35; }
.authors { margin: 8px 0 0; color: var(--color-text-secondary); }
.paper-meta { display: flex; flex-wrap: wrap; gap: 8px 18px; margin-top: 12px; color: var(--color-text-tertiary); font-size: 12px; }
.run-alert { margin: 0; }
.report-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.report-card-wide { grid-column: 1 / -1; }
.report-card header { display: flex; gap: 12px; align-items: flex-start; padding-bottom: 14px; border-bottom: 1px solid var(--gray-100); }
.stage-index { color: var(--main-color); font-size: 12px; font-weight: 700; letter-spacing: .08em; }
.report-card h2 { margin: 0; color: var(--color-text); font-size: 17px; }
.report-card header p { margin: 3px 0 0; color: var(--color-text-tertiary); font-size: 12px; }
.report-body { display: flex; flex-direction: column; gap: 14px; padding-top: 16px; }
.structure-body { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.report-body label { display: block; margin-bottom: 5px; color: var(--color-text-tertiary); font-size: 12px; }
.report-body p { margin: 0; color: var(--color-text-secondary); line-height: 1.65; white-space: pre-wrap; }
.innovation-item, .gap-item { position: relative; padding: 12px 14px; border-radius: 8px; background: var(--gray-30); }
.innovation-item strong, .gap-item strong { display: block; padding-right: 56px; color: var(--color-text); line-height: 1.5; }
.innovation-item p, .gap-item p { margin-top: 5px; font-size: 13px; }
.innovation-item .ant-tag, .gap-item .ant-tag { position: absolute; top: 12px; right: 12px; margin: 0; }
.method-block + .method-block { padding-top: 4px; }
.future-direction-block { padding-top: 4px; }

@media (max-width: 900px) {
  .report-grid { grid-template-columns: 1fr; }
  .report-card-wide { grid-column: auto; }
}

@media (max-width: 640px) {
  .paper-heading-card { flex-direction: column; }
  .structure-body { grid-template-columns: 1fr; }
  h1 { font-size: 21px; }
}
</style>
