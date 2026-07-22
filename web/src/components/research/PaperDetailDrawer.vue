<template>
  <a-drawer
    :open="open"
    :width="drawerWidth"
    :destroy-on-close="true"
    placement="right"
    root-class-name="paper-detail-drawer"
    @close="emit('close')"
  >
    <template #title>
      <div class="drawer-title-row">
        <FileText :size="18" />
        <span>论文详情</span>
      </div>
    </template>

    <template #extra>
      <a-button
        v-if="canEdit && detail?.paper"
        class="lucide-icon-btn"
        :disabled="detailLoading"
        @click="openEditModal"
      >
        <template #icon><Pencil :size="15" /></template>
        校正元数据
      </a-button>
    </template>

    <div v-if="detailLoading" class="drawer-loading">
      <a-spin />
      <span>加载论文详情...</span>
    </div>

    <a-result
      v-else-if="detailError"
      status="error"
      title="论文详情加载失败"
      :sub-title="detailError"
    >
      <template #extra>
        <a-button @click="loadDetail">重新加载</a-button>
      </template>
    </a-result>

    <div v-else-if="detail?.paper" class="paper-detail-content">
      <header class="paper-summary">
        <div class="paper-summary-main">
          <h2>{{ detail.paper.title }}</h2>
          <p v-if="authorText" class="paper-authors">{{ authorText }}</p>
          <div class="paper-meta-row">
            <span v-if="detail.paper.publication_year">
              <CalendarDays :size="14" />
              {{ detail.paper.publication_year }}
            </span>
            <span v-if="detail.paper.venue">
              <Landmark :size="14" />
              {{ detail.paper.venue }}
            </span>
            <span v-if="detail.paper.citation_count !== null">
              <Quote :size="14" />
              被引 {{ detail.paper.citation_count }}
            </span>
          </div>
          <a
            v-if="detail.paper.doi"
            class="doi-link"
            :href="`https://doi.org/${detail.paper.doi}`"
            target="_blank"
            rel="noopener noreferrer"
          >
            DOI: {{ detail.paper.doi }}
            <ExternalLink :size="13" />
          </a>
        </div>
        <span class="metadata-status" :class="statusClass(detail.paper.metadata_status)">
          {{ statusLabel(detail.paper.metadata_status) }}
        </span>
      </header>

      <a-alert
        v-if="detail.paper.metadata_status === 'pending_reindex'"
        type="info"
        show-icon
        message="论文元数据正在同步到分块与检索索引"
      />
      <a-alert
        v-else-if="detail.paper.metadata_status === 'sync_failed'"
        type="error"
        show-icon
        message="论文元数据同步失败"
        :description="detail.paper.metadata_error || '请重新校正元数据后再次同步。'"
      />

      <section v-if="detail.paper.abstract" class="detail-section">
        <h3>摘要</h3>
        <p class="paper-abstract">{{ detail.paper.abstract }}</p>
      </section>

      <section class="detail-section analysis-section">
        <div class="section-heading-row">
          <div>
            <h3>论文分析报告</h3>
            <p>结构化提取、创新点、方法论与研究空白</p>
          </div>
          <div class="analysis-actions">
            <a-button
              v-if="analysisRun?.result"
              class="lucide-icon-btn"
              @click="openAnalysisReport"
            >
              独立报告页
            </a-button>
            <a-button type="primary" :loading="analysisStarting" @click="startAnalysis">
              {{ analysisRun?.status === 'running' || analysisRun?.status === 'pending' ? '分析中' : '生成报告' }}
            </a-button>
          </div>
        </div>
        <a-alert
          v-if="analysisRun?.status === 'failed'"
          type="error"
          show-icon
          message="论文分析失败"
          :description="analysisRun.error_message || '请重新生成报告'"
        />
        <div v-else-if="analysisRun?.status === 'pending' || analysisRun?.status === 'running'" class="analysis-progress">
          <a-spin size="small" />
          <span>正在执行 {{ analysisRun.stage || '分析' }} 阶段...</span>
        </div>
        <div v-else-if="analysisRun?.result" class="analysis-result">
          <article>
            <h4>结构化摘要</h4>
            <p><strong>研究问题：</strong>{{ analysisRun.result.structure.problem }}</p>
            <p><strong>方法：</strong>{{ analysisRun.result.structure.method }}</p>
            <p><strong>结果：</strong>{{ formatAnalysisList(analysisRun.result.structure.results) }}</p>
          </article>
          <article>
            <h4>核心创新点</h4>
            <ul><li v-for="item in analysisRun.result.innovations.items" :key="item.claim">{{ item.claim }}<span v-if="item.evidence">（{{ item.evidence }}）</span></li></ul>
          </article>
          <article>
            <h4>方法论与复现性</h4>
            <p>{{ analysisRun.result.methodology.research_design }}</p>
            <p>复现信息：{{ analysisRun.result.methodology.reproducibility?.available ? '已提供' : '未明确提供' }}</p>
          </article>
          <article>
            <h4>研究空白与后续方向</h4>
            <ul><li v-for="item in analysisRun.result.research_gaps.gaps" :key="item.claim">{{ item.claim }}<span v-if="item.basis">（依据：{{ item.basis }}）</span></li></ul>
          </article>
        </div>
        <a-empty v-else description="尚未生成论文分析报告" />
      </section>

      <section v-if="detail.paper.keywords?.length" class="detail-section">
        <h3>关键词</h3>
        <div class="tag-row">
          <a-tag v-for="keyword in detail.paper.keywords" :key="keyword">{{ keyword }}</a-tag>
        </div>
      </section>

      <section class="detail-section section-browser">
        <div class="section-heading-row">
          <div>
            <h3>论文内容</h3>
            <p>{{ detail.file.chunk_count }} 个学术分块 · {{ detail.sections.length }} 个章节节点</p>
          </div>
          <a-button class="lucide-icon-btn" :loading="chunksLoading" @click="loadChunks">
            <template #icon><RefreshCw :size="14" /></template>
            刷新
          </a-button>
        </div>

        <div class="section-filter" role="navigation" aria-label="论文章节">
          <button
            type="button"
            :class="{ active: !activeSectionType }"
            @click="selectSection('')"
          >
            全部
            <span>{{ detail.file.chunk_count }}</span>
          </button>
          <button
            v-for="section in availableSections"
            :key="section.section_type"
            type="button"
            :class="{ active: activeSectionType === section.section_type }"
            @click="selectSection(section.section_type)"
          >
            {{ sectionLabel(section.section_type) }}
            <span>{{ detail.section_counts[section.section_type] || 0 }}</span>
          </button>
        </div>

        <div v-if="chunksLoading" class="chunks-loading">
          <a-spin size="small" />
          <span>加载章节内容...</span>
        </div>
        <a-alert v-else-if="chunksError" type="error" show-icon :message="chunksError" />
        <a-empty v-else-if="chunks.length === 0" description="该章节暂无可用内容" />
        <div v-else class="chunk-list">
          <article v-for="chunk in chunks" :key="chunk.chunk_id" class="chunk-card">
            <div class="chunk-card-header">
              <div class="chunk-location">
                <span>#{{ chunk.chunk_index + 1 }}</span>
                <span>{{ sectionLabel(chunk.metadata?.section_type) }}</span>
                <span v-if="chunk.metadata?.section_title">{{ chunk.metadata.section_title }}</span>
              </div>
              <span v-if="chunk.start_char_pos !== null" class="source-position">
                字符 {{ chunk.start_char_pos }}–{{ chunk.end_char_pos }}
              </span>
              <span v-if="chunk.metadata?.source_page_start" class="source-position">
                PDF 页码 {{ chunk.metadata.source_page_start }}<template v-if="chunk.metadata.source_page_end && chunk.metadata.source_page_end !== chunk.metadata.source_page_start">–{{ chunk.metadata.source_page_end }}</template>
              </span>
            </div>
            <pre>{{ chunk.content }}</pre>
            <div v-if="chunk.metadata?.element_types?.length" class="tag-row compact">
              <a-tag v-for="element in chunk.metadata.element_types" :key="element">
                {{ elementLabel(element) }}
              </a-tag>
            </div>
          </article>
        </div>

        <a-pagination
          v-if="chunksTotal > chunkPageSize"
          v-model:current="chunkPage"
          :page-size="chunkPageSize"
          :total="chunksTotal"
          :show-size-changer="false"
          size="small"
          @change="loadChunks"
        />
      </section>
    </div>

    <a-modal
      v-model:open="editModalOpen"
      title="校正论文元数据"
      :confirm-loading="saving"
      width="680px"
      ok-text="保存并重建检索索引"
      cancel-text="取消"
      @ok="saveMetadata"
    >
      <a-alert
        type="info"
        show-icon
        message="保存后系统会在后台重新生成学术分块与检索索引，原文文件不会被修改。"
        class="edit-info"
      />
      <a-form ref="editFormRef" :model="editForm" :rules="editRules" layout="vertical">
        <a-form-item label="论文标题" name="title">
          <a-input v-model:value="editForm.title" :maxlength="2000" show-count />
        </a-form-item>
        <a-form-item label="作者" name="authorsText">
          <a-textarea
            v-model:value="editForm.authorsText"
            :rows="2"
            placeholder="多位作者请用英文逗号分隔"
          />
        </a-form-item>
        <div class="two-column-form">
          <a-form-item label="发表年份" name="publication_year">
            <a-input-number
              v-model:value="editForm.publication_year"
              :min="1500"
              :max="maxPublicationYear"
              class="full-width"
            />
          </a-form-item>
          <a-form-item label="引用数" name="citation_count">
            <a-input-number
              v-model:value="editForm.citation_count"
              :min="0"
              class="full-width"
            />
          </a-form-item>
        </div>
        <a-form-item label="期刊 / 会议" name="venue">
          <a-input v-model:value="editForm.venue" :maxlength="512" />
        </a-form-item>
        <a-form-item label="DOI" name="doi">
          <a-input v-model:value="editForm.doi" :maxlength="512" placeholder="10.xxxx/xxxxx" />
        </a-form-item>
        <a-form-item label="关键词" name="keywordsText">
          <a-input
            v-model:value="editForm.keywordsText"
            placeholder="多个关键词请用英文逗号分隔"
          />
        </a-form-item>
        <a-form-item label="摘要" name="abstract">
          <a-textarea v-model:value="editForm.abstract" :rows="7" :maxlength="200000" />
        </a-form-item>
      </a-form>
    </a-modal>
  </a-drawer>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { useRouter } from 'vue-router'
import {
  CalendarDays,
  ExternalLink,
  FileText,
  Landmark,
  Pencil,
  Quote,
  RefreshCw
} from 'lucide-vue-next'
import { researchApi } from '@/apis/research_api'

const props = defineProps({
  open: { type: Boolean, default: false },
  kbId: { type: String, default: '' },
  paperId: { type: String, default: '' },
  canEdit: { type: Boolean, default: false }
})

const emit = defineEmits(['close', 'updated'])
const router = useRouter()

const detail = ref(null)
const detailLoading = ref(false)
const detailError = ref('')
const chunks = ref([])
const chunksLoading = ref(false)
const chunksError = ref('')
const chunksTotal = ref(0)
const chunkPage = ref(1)
const chunkPageSize = 20
const activeSectionType = ref('')
const editModalOpen = ref(false)
const editFormRef = ref(null)
const saving = ref(false)
const maxPublicationYear = new Date().getFullYear() + 1
const drawerWidth = 'min(780px, 100vw)'
const analysisRun = ref(null)
const analysisStarting = ref(false)
let analysisTimer = null

const editForm = reactive({
  title: '',
  authorsText: '',
  publication_year: null,
  citation_count: null,
  venue: '',
  doi: '',
  keywordsText: '',
  abstract: ''
})

const editRules = {
  title: [{ required: true, whitespace: true, message: '请输入论文标题' }]
}

const sectionLabels = {
  abstract: '摘要',
  introduction: '引言',
  related_work: '相关工作',
  methodology: '方法',
  experiments: '实验',
  discussion: '讨论',
  conclusion: '结论',
  limitations: '局限性',
  references: '参考文献',
  appendix: '附录',
  other: '其他'
}

const statusLabels = {
  extracted: '已提取',
  verified: '已校正',
  pending_reindex: '同步中',
  sync_failed: '同步失败'
}

const authorText = computed(() => (detail.value?.paper?.authors || []).join('、'))
const availableSections = computed(() => {
  const seen = new Set()
  return (detail.value?.sections || []).filter((section) => {
    if (seen.has(section.section_type)) return false
    seen.add(section.section_type)
    return true
  })
})

const statusLabel = (status) => statusLabels[status] || status || '未知状态'
const statusClass = (status) => `status-${status || 'unknown'}`
const sectionLabel = (type) => sectionLabels[type] || type || '其他'
const elementLabel = (element) => ({ formula: '公式', table: '表格', code: '代码' })[element] || element
const formatAnalysisList = (value) => Array.isArray(value) ? value.join('；') : String(value || '未明确')

const loadDetail = async () => {
  if (!props.open || !props.kbId || !props.paperId) return
  detailLoading.value = true
  detailError.value = ''
  try {
    detail.value = await researchApi.getPaper(props.kbId, props.paperId)
    await loadChunks()
    await loadLatestAnalysis()
  } catch (error) {
    detailError.value = error.message || '无法加载论文详情'
  } finally {
    detailLoading.value = false
  }
}

const loadLatestAnalysis = async () => {
  if (analysisTimer) clearTimeout(analysisTimer)
  analysisStarting.value = false
  try {
    analysisRun.value = await researchApi.getLatestPaperAnalysis(props.kbId, props.paperId)
    if (analysisRun.value?.run_id && ['pending', 'running'].includes(analysisRun.value.status)) {
      await pollAnalysis(analysisRun.value.run_id)
    }
  } catch {
    analysisRun.value = null
  }
}

const pollAnalysis = async (runId) => {
  try {
    analysisRun.value = await researchApi.getPaperAnalysisRun(runId)
    if (['pending', 'running'].includes(analysisRun.value.status)) {
      analysisTimer = setTimeout(() => pollAnalysis(runId), 2000)
    } else {
      analysisStarting.value = false
    }
  } catch (error) {
    analysisStarting.value = false
    analysisRun.value = { status: 'failed', error_message: error.message || '论文分析状态查询失败' }
  }
}

const startAnalysis = async () => {
  if (analysisStarting.value) return
  analysisStarting.value = true
  try {
    const result = await researchApi.analyzePaper(props.kbId, props.paperId)
    await pollAnalysis(result.run_id)
  } catch (error) {
    analysisStarting.value = false
    analysisRun.value = { status: 'failed', error_message: error.message || '论文分析任务提交失败' }
  }
}

const openAnalysisReport = () => {
  if (!props.kbId || !props.paperId) return
  emit('close')
  router.push({
    name: 'ResearchPaperAnalysis',
    params: { kbId: props.kbId, paperId: props.paperId }
  })
}

const loadChunks = async () => {
  if (!props.kbId || !props.paperId) return
  chunksLoading.value = true
  chunksError.value = ''
  try {
    const result = await researchApi.listPaperChunks(props.kbId, props.paperId, {
      section_type: activeSectionType.value,
      offset: (chunkPage.value - 1) * chunkPageSize,
      limit: chunkPageSize
    })
    chunks.value = result.items || []
    chunksTotal.value = result.total || 0
  } catch (error) {
    chunksError.value = error.message || '章节内容加载失败'
  } finally {
    chunksLoading.value = false
  }
}

const selectSection = (sectionType) => {
  activeSectionType.value = sectionType
  chunkPage.value = 1
  loadChunks()
}

const openEditModal = () => {
  const paper = detail.value?.paper
  if (!paper) return
  Object.assign(editForm, {
    title: paper.title || '',
    authorsText: (paper.authors || []).join(', '),
    publication_year: paper.publication_year,
    citation_count: paper.citation_count,
    venue: paper.venue || '',
    doi: paper.doi || '',
    keywordsText: (paper.keywords || []).join(', '),
    abstract: paper.abstract || ''
  })
  editModalOpen.value = true
}

const parseCommaList = (value) =>
  String(value || '')
    .split(/[,，;；]/)
    .map((item) => item.trim())
    .filter(Boolean)

const saveMetadata = async () => {
  try {
    await editFormRef.value?.validate()
  } catch {
    return
  }

  saving.value = true
  try {
    const result = await researchApi.updatePaper(props.kbId, props.paperId, {
      title: editForm.title.trim(),
      authors: parseCommaList(editForm.authorsText),
      publication_year: editForm.publication_year,
      citation_count: editForm.citation_count,
      venue: editForm.venue.trim() || null,
      doi: editForm.doi.trim() || null,
      keywords: parseCommaList(editForm.keywordsText),
      abstract: editForm.abstract.trim() || null
    })
    editModalOpen.value = false
    detail.value.paper = result.paper
    message.success('元数据已保存，检索索引正在后台同步')
    emit('updated', result)
  } catch (error) {
    message.error(error.message || '论文元数据保存失败')
  } finally {
    saving.value = false
  }
}

watch(
  () => [props.open, props.kbId, props.paperId],
  ([isOpen]) => {
    if (!isOpen) {
      if (analysisTimer) clearTimeout(analysisTimer)
      analysisTimer = null
      detail.value = null
      chunks.value = []
      analysisRun.value = null
      return
    }
    activeSectionType.value = ''
    chunkPage.value = 1
    loadDetail()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  if (analysisTimer) clearTimeout(analysisTimer)
})
</script>

<style scoped lang="less">
.drawer-title-row,
.paper-meta-row span,
.doi-link,
.section-heading-row,
.chunk-card-header,
.drawer-loading,
.chunks-loading {
  display: flex;
  align-items: center;
}

.drawer-title-row {
  gap: 8px;
}

.drawer-loading {
  min-height: 280px;
  justify-content: center;
  gap: 10px;
  color: var(--color-text-secondary);
}

.paper-detail-content {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.paper-summary {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--gray-150);
}

.paper-summary-main {
  min-width: 0;

  h2 {
    margin: 0;
    color: var(--color-text);
    font-size: 21px;
    line-height: 1.45;
    font-weight: 600;
  }
}

.paper-authors {
  margin: 8px 0 0;
  color: var(--color-text-secondary);
  font-size: 14px;
}

.paper-meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-top: 12px;
  color: var(--color-text-secondary);
  font-size: 13px;

  span {
    gap: 5px;
  }
}

.doi-link {
  width: fit-content;
  gap: 5px;
  margin-top: 10px;
  color: var(--main-color);
  font-size: 13px;
}

.metadata-status {
  flex: none;
  padding: 3px 9px;
  border-radius: 999px;
  color: var(--gray-600);
  background: var(--gray-100);
  font-size: 12px;
  font-weight: 500;

  &.status-verified {
    color: var(--color-success-700);
    background: var(--color-success-50);
  }

  &.status-pending_reindex {
    color: var(--color-info-700);
    background: var(--color-info-50);
  }

  &.status-sync_failed {
    color: var(--color-error-700);
    background: var(--color-error-50);
  }
}

.detail-section {
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);

  h3 {
    margin: 0 0 10px;
    color: var(--color-text);
    font-size: 16px;
    font-weight: 600;
  }
}

.paper-abstract {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.75;
  white-space: pre-wrap;
}

.analysis-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.analysis-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.analysis-result {
  display: grid;
  gap: 10px;

  article {
    padding: 12px;
    border-radius: 6px;
    background: var(--gray-25);
  }

  h4 {
    margin: 0 0 7px;
    color: var(--color-text);
    font-size: 14px;
  }

  p,
  li {
    margin: 4px 0;
    color: var(--color-text-secondary);
    font-size: 13px;
    line-height: 1.6;
  }

  ul {
    padding-left: 18px;
    margin: 0;
  }
}

.tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;

  &.compact {
    margin-top: 10px;
  }
}

.section-heading-row {
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;

  h3 {
    margin-bottom: 3px;
  }

  p {
    margin: 0;
    color: var(--color-text-tertiary);
    font-size: 12px;
  }
}

.section-filter {
  display: flex;
  gap: 6px;
  padding-bottom: 12px;
  margin-bottom: 12px;
  overflow-x: auto;
  border-bottom: 1px solid var(--gray-100);

  button {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    flex: none;
    min-height: 30px;
    padding: 0 9px;
    border: 1px solid var(--gray-150);
    border-radius: 6px;
    color: var(--color-text-secondary);
    background: var(--gray-0);
    cursor: pointer;

    &:hover,
    &.active {
      color: var(--main-color);
      border-color: var(--main-200);
      background: var(--main-30);
    }

    span {
      color: var(--color-text-tertiary);
      font-size: 11px;
    }
  }
}

.chunks-loading {
  justify-content: center;
  gap: 8px;
  min-height: 120px;
  color: var(--color-text-secondary);
}

.chunk-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 14px;
}

.chunk-card {
  padding: 12px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  background: var(--gray-10);

  pre {
    margin: 10px 0 0;
    color: var(--color-text);
    font-family: inherit;
    font-size: 13px;
    line-height: 1.65;
    white-space: pre-wrap;
    word-break: break-word;
  }
}

.chunk-card-header {
  justify-content: space-between;
  gap: 10px;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.chunk-location {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 10px;
}

.source-position {
  flex: none;
  font-family: monospace;
}

.edit-info {
  margin-bottom: 16px;
}

.two-column-form {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.full-width {
  width: 100%;
}

@media (max-width: 640px) {
  .paper-summary {
    flex-direction: column;
  }

  .two-column-form {
    grid-template-columns: 1fr;
    gap: 0;
  }

  .chunk-card-header {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
