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
      <div class="drawer-extra-actions">
        <a-button
          v-if="detail?.paper"
          class="lucide-icon-btn"
          :disabled="detailLoading"
          @click="exportSingleBibtex"
        >
          <template #icon><Download :size="15" /></template>
          BibTeX
        </a-button>
        <a-button
          v-if="canEdit && detail?.paper"
          class="lucide-icon-btn"
          :loading="enriching"
          :disabled="detailLoading"
          @click="enrichMetadata"
        >
          <template #icon><Sparkles :size="15" /></template>
          补全元数据
        </a-button>
        <a-button
          v-if="canEdit && detail?.paper"
          class="lucide-icon-btn"
          :disabled="detailLoading"
          @click="openEditModal"
        >
          <template #icon><Pencil :size="15" /></template>
          校正元数据
        </a-button>
      </div>
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

      <section class="detail-section">
        <div class="section-heading-row">
          <h3>我的标签</h3>
        </div>
        <div class="tag-manager">
          <div class="tag-display-row">
            <a-tag
              v-for="tag in paperTags"
              :key="tag"
              closable
              @close="removeTag(tag)"
            >{{ tag }}</a-tag>
            <span v-if="paperTags.length === 0" class="no-tags-hint">暂无标签</span>
          </div>
          <div class="tag-input-row">
            <a-input
              v-model:value="newTag"
              placeholder="输入标签后回车"
              style="width: 200px"
              :maxlength="64"
              @press-enter="addTag"
            />
            <a-button size="small" @click="addTag">添加</a-button>
          </div>
        </div>
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
            <a-button
              type="primary"
              :loading="analysisStarting"
              :disabled="!detail.paper.has_fulltext"
              :title="!detail.paper.has_fulltext ? '仅元数据论文无法生成分析报告' : ''"
              @click="startAnalysis"
            >
              {{ analysisRun?.status === 'running' || analysisRun?.status === 'pending' ? '分析中' : '生成报告' }}
            </a-button>
          </div>
        </div>
        <a-alert
          v-if="!detail.paper.has_fulltext"
          type="info"
          show-icon
          message="该论文仅含元数据，无法生成分析报告"
        />
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

      <section class="detail-section">
        <div class="section-heading-row">
          <div>
            <h3>引文滚雪球</h3>
            <p>把这篇论文的参考文献或被引文献纳入本地库</p>
          </div>
          <div class="analysis-actions">
            <a-button
              :loading="relatedLoading && relatedDirection === 'references'"
              @click="openRelated('references')"
            >
              参考文献
            </a-button>
            <a-button
              :loading="relatedLoading && relatedDirection === 'citations'"
              @click="openRelated('citations')"
            >
              被引文献
            </a-button>
          </div>
        </div>
        <a-alert v-if="relatedError" type="error" show-icon :message="relatedError" />
      </section>

      <section class="detail-section">
        <div class="section-heading-row">
          <div>
            <h3>对照翻译</h3>
            <p>按片段生成中英对照，保留页码定位；译文不写入数据库与证据库</p>
          </div>
          <div class="analysis-actions">
            <a-select
              v-model:value="translationSection"
              :options="translationSectionOptions"
              placeholder="全部章节"
              allow-clear
              style="width: 130px"
            />
            <a-button
              :loading="translationLoading"
              :disabled="!detail.paper.has_fulltext"
              @click="translatePaper"
            >
              生成对照
            </a-button>
          </div>
        </div>
        <a-alert v-if="translationError" type="error" show-icon :message="translationError" />
        <p
          v-if="translationLoading"
          class="translation-loading-hint"
          role="status"
          aria-live="polite"
        >
          正在逐段翻译，约需 1–2 分钟，请保持页面打开。
        </p>
        <div v-if="translationResult?.paragraphs?.length" class="translation-list">
          <article v-for="item in translationResult.paragraphs" :key="item.chunk_id" class="translation-item">
            <header>
              <a-tag v-if="item.page_start">第 {{ item.page_start }} 页</a-tag>
              <a-tag v-else color="default">页码未提供</a-tag>
              <span v-if="item.section_title">{{ item.section_title }}</span>
            </header>
            <p class="translation-source">{{ item.source }}</p>
            <p class="translation-text">{{ item.translation }}</p>
          </article>
          <p class="translation-note">{{ translationResult.note }}</p>
        </div>
      </section>

      <section v-if="detail.paper.keywords?.length" class="detail-section">
        <h3>关键词</h3>
        <div class="tag-row">
          <a-tag v-for="keyword in detail.paper.keywords" :key="keyword">{{ keyword }}</a-tag>
        </div>
      </section>

      <a-alert
        v-if="!detail.paper.has_fulltext"
        type="info"
        show-icon
        message="该论文由参考文献文件导入，仅含元数据，不参与全文检索"
        description="可尝试从开放获取源补全全文；补全后需要解析与索引，完成后刷新查看。"
        style="margin-bottom: 12px;"
      >
        <template #action>
          <a-button
            v-if="canEdit && detail.paper.doi"
            size="small"
            :loading="fulltextLoading"
            @click="importFullText"
          >补全文</a-button>
        </template>
      </a-alert>

      <section class="detail-section note-section">
        <div class="section-heading-row">
          <div>
            <h3>阅读笔记</h3>
            <p>{{ notes.length }} 条 · 仅自己可见</p>
          </div>
          <a-button
            size="small"
            :disabled="notes.length === 0"
            :loading="noteExporting"
            @click="exportNotes"
          >
            <template #icon><Download :size="14" /></template>
            导出
          </a-button>
        </div>

        <a-alert v-if="notesError" type="error" show-icon :message="notesError" />

        <div v-if="annotationDraft" class="annotation-draft">
          <div class="annotation-draft-quote">
            <Quote :size="14" />
            <span>{{ annotationDraft.quote }}</span>
          </div>
          <span v-if="annotationDraft.page" class="source-position">PDF 第 {{ annotationDraft.page }} 页</span>
          <a-button type="link" size="small" @click="annotationDraft = null">取消批注</a-button>
        </div>

        <a-textarea
          v-model:value="noteDraft"
          :rows="3"
          :maxlength="10000"
          placeholder="写下阅读结论、疑问或批注..."
        />
        <div class="note-submit-row">
          <span class="note-hint">在下方章节内容中选中文字可直接添加批注</span>
          <a-button
            type="primary"
            :loading="noteSubmitting"
            :disabled="!noteDraft.trim()"
            @click="submitNote"
          >添加笔记</a-button>
        </div>

        <a-spin v-if="notesLoading" size="small" />
        <a-empty v-else-if="notes.length === 0" description="还没有笔记" />
        <div v-else class="note-list">
          <article
            v-for="note in notes"
            :key="note.note_id"
            class="note-card"
            :class="{ active: activeNoteId === note.note_id }"
            :data-note-id="note.note_id"
            @click="activeNoteId = note.note_id"
          >
            <div class="note-card-head">
              <span v-if="note.page" class="source-position">PDF 第 {{ note.page }} 页</span>
              <span class="source-position">{{ formatNoteTime(note.created_at) }}</span>
              <div class="note-card-actions">
                <a-button type="link" size="small" @click.stop="startEditNote(note)">编辑</a-button>
                <a-popconfirm title="删除这条笔记？" @confirm="removeNote(note)">
                  <a-button type="link" size="small" danger @click.stop>删除</a-button>
                </a-popconfirm>
              </div>
            </div>
            <blockquote v-if="note.quote" class="note-quote">{{ note.quote }}</blockquote>
            <template v-if="editingNoteId === note.note_id">
              <a-textarea v-model:value="editingNoteContent" :rows="3" />
              <div class="note-submit-row">
                <a-button size="small" @click="cancelEditNote">取消</a-button>
                <a-button type="primary" size="small" :loading="noteSubmitting" @click="saveEditedNote(note)">
                  保存
                </a-button>
              </div>
            </template>
            <p v-else class="note-content">{{ note.content }}</p>
          </article>
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
            <div
              class="chunk-content"
              :data-chunk-id="chunk.chunk_id"
              @mouseup="handleChunkSelection(chunk, $event)"
            >
              <template v-for="(segment, index) in chunkSegments(chunk)" :key="`${chunk.chunk_id}-${index}`">
                <mark
                  v-if="segment.noteId"
                  class="chunk-highlight"
                  :class="{ active: activeNoteId === segment.noteId }"
                  @click.stop="focusNote(segment.noteId)"
                >{{ segment.text }}</mark>
                <span v-else>{{ segment.text }}</span>
              </template>
            </div>
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

    <a-modal
      v-model:open="relatedOpen"
      :title="relatedDirection === 'references' ? '导入参考文献' : '导入被引文献'"
      :confirm-loading="relatedImporting"
      width="760px"
      ok-text="导入所选"
      :ok-button-props="{ disabled: relatedSelectedIds.length === 0 }"
      @ok="importSelectedRelated"
    >
      <p class="related-hint">
        共 {{ relatedItems.length }} 条，已在库 {{ relatedExistingCount }} 条。默认只导入元数据；开启全文下载时，仅有开放获取地址的条目会进入下载与索引队列。
      </p>
      <a-alert v-if="relatedError" type="error" show-icon :message="relatedError" />
      <a-spin v-if="relatedLoading" />
      <a-empty v-else-if="relatedItems.length === 0" description="没有可导入的关联文献" />
      <div v-else class="related-list">
        <a-checkbox
          :checked="relatedAllSelected"
          :indeterminate="relatedSelectedIds.length > 0 && !relatedAllSelected"
          @change="toggleAllRelated"
        >
          全选可导入条目
        </a-checkbox>
        <label
          v-for="item in relatedItems"
          :key="item.s2_paper_id"
          class="related-item"
          :class="{ disabled: item.already_in_library }"
        >
          <a-checkbox
            :checked="relatedSelectedIds.includes(item.s2_paper_id)"
            :disabled="item.already_in_library"
            @change="toggleRelatedSelection(item.s2_paper_id)"
          />
          <div>
            <strong>{{ item.title }}</strong>
            <p>
              {{ item.authors?.slice(0, 3).join('、') || '作者未知' }}
              <span v-if="item.publication_year"> · {{ item.publication_year }}</span>
              <span v-if="item.venue"> · {{ item.venue }}</span>
            </p>
            <div class="related-tags">
              <a-tag v-if="item.already_in_library" color="green">已在库</a-tag>
              <a-tag v-if="item.is_open_access" color="blue">开放获取</a-tag>
            </div>
          </div>
        </label>
      </div>
      <div class="related-switch-row">
        <a-switch v-model:checked="relatedIncludeFullText" />
        <span>开放获取条目同时下载全文（不限数量时建议分批导入）</span>
      </div>
    </a-modal>
  </a-drawer>
</template>

<script setup>
// ResearchCompass 论文详情抽屉
// 本组件是本仓库在开源智能体框架 Yuxi 之上实现的科研业务界面，负责展示论文元数据、
// 摘要、自定义标签、论文章节分块浏览与四阶段分析报告，并支持管理员校正元数据后
// 触发后台重建学术分块与检索索引。通用抽屉、表单与请求基础设施由 Yuxi 提供。
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { useRouter } from 'vue-router'
import {
  CalendarDays,
  Download,
  ExternalLink,
  FileText,
  Landmark,
  Pencil,
  Quote,
  RefreshCw,
  Sparkles
} from 'lucide-vue-next'
import { researchApi } from '@/apis/research_api'

const props = defineProps({
  open: { type: Boolean, default: false },
  kbId: { type: String, default: '' },
  paperId: { type: String, default: '' },
  canEdit: { type: Boolean, default: false }
})

const emit = defineEmits(['close', 'updated', 'library-changed'])
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
const enriching = ref(false)
const maxPublicationYear = new Date().getFullYear() + 1
const drawerWidth = 'min(780px, 100vw)'
const analysisRun = ref(null)
const analysisStarting = ref(false)
let analysisTimer = null
const paperTags = ref([])
const newTag = ref('')
const relatedOpen = ref(false)
const relatedDirection = ref('references')
const relatedItems = ref([])
const relatedExistingCount = ref(0)
const relatedSelectedIds = ref([])
const relatedLoading = ref(false)
const relatedImporting = ref(false)
const relatedIncludeFullText = ref(false)
const relatedError = ref('')
const translationSection = ref(undefined)
const translationLoading = ref(false)
const translationError = ref('')
const translationResult = ref(null)
const notes = ref([])
const notesLoading = ref(false)
const notesError = ref('')
const noteDraft = ref('')
const noteSubmitting = ref(false)
const noteExporting = ref(false)
const annotationDraft = ref(null)
const editingNoteId = ref('')
const editingNoteContent = ref('')
const activeNoteId = ref('')
const fulltextLoading = ref(false)

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
const translationSectionOptions = computed(() =>
  availableSections.value.map((section) => ({
    value: section.section_type,
    label: sectionLabel(section.section_type)
  }))
)
const elementLabel = (element) => ({ formula: '公式', table: '表格', code: '代码' })[element] || element
const formatAnalysisList = (value) => Array.isArray(value) ? value.join('；') : String(value || '未明确')

const importFullText = async () => {
  const doi = detail.value?.paper?.doi
  if (!doi || fulltextLoading.value) return
  const identifier = /^10\.48550\/arxiv\./i.test(doi)
    ? `arXiv:${doi.split('/').pop().replace(/^arxiv\./i, '').replace(/v\d+$/i, '')}`
    : doi
  fulltextLoading.value = true
  try {
    await researchApi.importExternalPaper(props.kbId, identifier)
    message.success('已提交全文下载任务，完成后刷新查看分块')
  } catch (error) {
    message.error(error.message || '提交全文下载失败')
  } finally {
    fulltextLoading.value = false
  }
}

const loadNotes = async () => {
  if (!props.kbId || !props.paperId) return
  notesLoading.value = true
  notesError.value = ''
  try {
    const result = await researchApi.listPaperNotes(props.kbId, props.paperId)
    notes.value = result.items || []
  } catch (error) {
    notesError.value = error.message || '笔记加载失败'
  } finally {
    notesLoading.value = false
  }
}

const submitNote = async () => {
  const content = noteDraft.value.trim()
  if (!content || noteSubmitting.value) return
  noteSubmitting.value = true
  try {
    const note = await researchApi.createPaperNote(props.kbId, props.paperId, {
      content,
      ...(annotationDraft.value || {})
    })
    notes.value.push(note)
    noteDraft.value = ''
    annotationDraft.value = null
    activeNoteId.value = note.note_id
    message.success('笔记已添加')
  } catch (error) {
    message.error(error.message || '添加笔记失败')
  } finally {
    noteSubmitting.value = false
  }
}

const startEditNote = (note) => {
  editingNoteId.value = note.note_id
  editingNoteContent.value = note.content
}

const cancelEditNote = () => {
  editingNoteId.value = ''
  editingNoteContent.value = ''
}

const saveEditedNote = async (note) => {
  const content = editingNoteContent.value.trim()
  if (!content || noteSubmitting.value) return
  noteSubmitting.value = true
  try {
    const updated = await researchApi.updatePaperNote(props.kbId, props.paperId, note.note_id, content)
    const index = notes.value.findIndex((item) => item.note_id === note.note_id)
    if (index >= 0) notes.value[index] = updated
    cancelEditNote()
    message.success('笔记已保存')
  } catch (error) {
    message.error(error.message || '保存笔记失败')
  } finally {
    noteSubmitting.value = false
  }
}

const removeNote = async (note) => {
  try {
    await researchApi.deletePaperNote(props.kbId, props.paperId, note.note_id)
    notes.value = notes.value.filter((item) => item.note_id !== note.note_id)
    if (activeNoteId.value === note.note_id) activeNoteId.value = ''
    if (editingNoteId.value === note.note_id) cancelEditNote()
    message.success('笔记已删除')
  } catch (error) {
    message.error(error.message || '删除笔记失败')
  }
}

const exportNotes = async () => {
  if (!notes.value.length || noteExporting.value) return
  noteExporting.value = true
  try {
    const blob = await researchApi.exportPaperNotes(props.kbId, props.paperId)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `paper_notes_${props.paperId}.md`
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    message.error(error.message || '导出笔记失败')
  } finally {
    noteExporting.value = false
  }
}

const handleChunkSelection = (chunk, event) => {
  const selection = window.getSelection()
  if (!selection || selection.isCollapsed || !selection.rangeCount) return
  const range = selection.getRangeAt(0)
  const container = event.currentTarget
  if (!container || !container.contains(range.commonAncestorContainer)) return
  const quote = range.toString().replace(/\s+/g, ' ').trim()
  if (!quote) return
  const preRange = document.createRange()
  preRange.selectNodeContents(container)
  preRange.setEnd(range.startContainer, range.startOffset)
  const start = preRange.toString().length
  const end = start + range.toString().length
  if (end <= start) return
  annotationDraft.value = {
    quote: quote.slice(0, 5000),
    chunk_id: chunk.chunk_id,
    page: chunk.metadata?.source_page_start || null,
    start_offset: start,
    end_offset: end
  }
  noteDraft.value = ''
  selection.removeAllRanges()
  message.info(`已选中 ${quote.length} 字，可添加批注`)
}

const chunkSegments = (chunk) => {
  const content = chunk.content || ''
  const chunkNotes = notes.value
    .filter(
      (note) =>
        note.chunk_id === chunk.chunk_id &&
        Number.isInteger(note.start_offset) &&
        Number.isInteger(note.end_offset) &&
        note.start_offset >= 0 &&
        note.start_offset < note.end_offset &&
        note.end_offset <= content.length
    )
    .sort((a, b) => a.start_offset - b.start_offset)
  if (!chunkNotes.length) return [{ text: content, noteId: '' }]
  const segments = []
  let cursor = 0
  for (const note of chunkNotes) {
    if (note.start_offset < cursor) continue
    if (note.start_offset > cursor) segments.push({ text: content.slice(cursor, note.start_offset), noteId: '' })
    segments.push({ text: content.slice(note.start_offset, note.end_offset), noteId: note.note_id })
    cursor = note.end_offset
  }
  if (cursor < content.length) segments.push({ text: content.slice(cursor), noteId: '' })
  return segments
}

const focusNote = (noteId) => {
  activeNoteId.value = noteId
  const element = document.querySelector(`[data-note-id="${noteId}"]`)
  element?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}

const formatNoteTime = (value) => (value ? String(value).slice(0, 16).replace('T', ' ') : '')

const loadDetail = async () => {
  if (!props.open || !props.kbId || !props.paperId) return
  detailLoading.value = true
  detailError.value = ''
  try {
    detail.value = await researchApi.getPaper(props.kbId, props.paperId)
    await loadChunks()
    await loadLatestAnalysis()
    await loadPaperTags()
    await loadNotes()
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

const loadPaperTags = async () => {
  if (!props.kbId || !props.paperId) return
  try {
    const result = await researchApi.listPaperTags(props.kbId, props.paperId)
    paperTags.value = result.tags || []
  } catch {
    paperTags.value = []
  }
}

const addTag = async () => {
  const tag = newTag.value.trim()
  if (!tag) return
  try {
    await researchApi.addPaperTag(props.kbId, props.paperId, tag)
    if (!paperTags.value.includes(tag)) paperTags.value.push(tag)
    newTag.value = ''
  } catch (error) {
    message.error(error.message || '添加标签失败')
  }
}

const removeTag = async (tag) => {
  try {
    await researchApi.removePaperTag(props.kbId, props.paperId, tag)
    paperTags.value = paperTags.value.filter((t) => t !== tag)
  } catch (error) {
    message.error(error.message || '删除标签失败')
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

const exportSingleBibtex = async () => {
  if (!props.kbId || !props.paperId) return
  try {
    const blob = await researchApi.exportPapersBibtex(props.kbId, [props.paperId])
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${props.paperId}.bib`
    a.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    message.error(error.message || 'BibTeX 导出失败')
  }
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

const enrichMetadata = async () => {
  if (!props.kbId || !props.paperId || enriching.value) return
  enriching.value = true
  try {
    const result = await researchApi.enrichPaperMetadata(props.kbId, props.paperId)
    if (result.paper) detail.value.paper = result.paper
    const filledFields = Object.keys(result.filled || {})
    if (filledFields.length) {
      message.success(result.message || `已补全 ${filledFields.length} 个元数据字段`)
      emit('updated', result)
    } else {
      message.info(result.message || '论文元数据已完整')
    }
  } catch (error) {
    message.error(error.message || '元数据补全失败')
  } finally {
    enriching.value = false
  }
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

const relatedAllSelected = computed(
  () =>
    relatedItems.value.some((item) => !item.already_in_library) &&
    relatedSelectedIds.value.length === relatedItems.value.filter((item) => !item.already_in_library).length
)

const openRelated = async (direction) => {
  relatedDirection.value = direction
  relatedOpen.value = true
  relatedLoading.value = true
  relatedError.value = ''
  relatedItems.value = []
  relatedSelectedIds.value = []
  relatedExistingCount.value = 0
  try {
    const result = await researchApi.listRelatedPapers(props.kbId, props.paperId, direction, 20)
    relatedItems.value = result.items || []
    relatedExistingCount.value = result.existing_count || 0
  } catch (error) {
    relatedError.value = error.message || '关联文献加载失败'
  } finally {
    relatedLoading.value = false
  }
}

const toggleRelatedSelection = (paperId) => {
  relatedSelectedIds.value = relatedSelectedIds.value.includes(paperId)
    ? relatedSelectedIds.value.filter((item) => item !== paperId)
    : [...relatedSelectedIds.value, paperId]
}

const toggleAllRelated = () => {
  relatedSelectedIds.value = relatedAllSelected.value
    ? []
    : relatedItems.value.filter((item) => !item.already_in_library).map((item) => item.s2_paper_id)
}

const translatePaper = async () => {
  if (!props.paperId || translationLoading.value) return
  translationLoading.value = true
  translationError.value = ''
  translationResult.value = null
  try {
    translationResult.value = await researchApi.translatePaper(props.kbId, props.paperId, {
      section_type: translationSection.value || null,
      limit: 6
    })
  } catch (error) {
    translationResult.value = null
    translationError.value = error.message || '对照翻译失败'
  } finally {
    translationLoading.value = false
  }
}

const importSelectedRelated = async () => {
  if (relatedSelectedIds.value.length === 0) return
  relatedImporting.value = true
  relatedError.value = ''
  try {
    const result = await researchApi.importRelatedPapers(props.kbId, props.paperId, {
      direction: relatedDirection.value,
      paper_ids: relatedSelectedIds.value,
      include_full_text: relatedIncludeFullText.value
    })
    message.success(
      `已导入元数据 ${result.metadata_imported} 条，全文入队 ${result.full_text_queued} 条，跳过 ${result.skipped} 条`
    )
    relatedOpen.value = false
    emit('library-changed')
  } catch (error) {
    relatedError.value = error.message || '关联文献导入失败'
  } finally {
    relatedImporting.value = false
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
      paperTags.value = []
      newTag.value = ''
      relatedOpen.value = false
      relatedItems.value = []
      relatedSelectedIds.value = []
      translationResult.value = null
      translationError.value = ''
      notes.value = []
      notesError.value = ''
      noteDraft.value = ''
      annotationDraft.value = null
      editingNoteId.value = ''
      editingNoteContent.value = ''
      activeNoteId.value = ''
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

.drawer-extra-actions {
  display: flex;
  gap: 8px;
}

.related-hint {
  margin: 0 0 10px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.related-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 420px;
  overflow-y: auto;
  margin-top: 10px;
}

.related-item {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 10px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  cursor: pointer;

  &.disabled {
    opacity: 0.6;
    cursor: default;
  }

  strong {
    display: block;
    color: var(--color-text);
    line-height: 1.45;
  }

  p {
    margin: 4px 0 0;
    color: var(--color-text-tertiary);
    font-size: 12px;
  }
}

.related-tags {
  display: flex;
  gap: 6px;
  margin-top: 6px;
}

.related-switch-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 14px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.translation-loading-hint {
  margin: 8px 0 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.translation-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 420px;
  margin-top: 12px;
  overflow-y: auto;
}

.translation-item {
  padding: 10px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;

  header {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-bottom: 6px;
    color: var(--color-text-tertiary);
    font-size: 12px;
  }
}

.translation-source {
  margin: 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1.6;
}

.translation-text {
  margin: 8px 0 0;
  color: var(--color-text);
  line-height: 1.7;
}

.translation-note {
  margin: 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
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

.note-section {
  .note-submit-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    margin-top: 8px;
  }

  .note-hint {
    color: var(--color-text-tertiary);
    font-size: 12px;
  }

  .annotation-draft {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 10px;
    margin-bottom: 8px;
    border: 1px solid var(--gray-150);
    border-radius: 6px;
    background: var(--gray-10);
  }

  .annotation-draft-quote {
    display: flex;
    align-items: flex-start;
    gap: 6px;
    flex: 1;
    min-width: 0;
    color: var(--color-text-tertiary);
    font-size: 12px;
  }

  .note-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-top: 12px;
  }

  .note-card {
    padding: 10px;
    border: 1px solid var(--gray-150);
    border-radius: 6px;
    background: var(--gray-10);
  }

  .note-card.active {
    border-color: var(--main-color);
    box-shadow: 0 0 0 1px var(--main-200);
  }

  .note-card-head {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .note-card-actions {
    margin-left: auto;
    display: flex;
    gap: 2px;
  }

  .note-quote {
    margin: 8px 0;
    padding-left: 8px;
    border-left: 2px solid var(--main-200);
    color: var(--color-text-tertiary);
    font-size: 12px;
  }

  .note-content {
    margin: 8px 0 0;
    color: var(--color-text);
    font-size: 13px;
    line-height: 1.65;
    white-space: pre-wrap;
    word-break: break-word;
  }
}

.chunk-content {
  margin: 10px 0 0;
  color: var(--color-text);
  font-family: inherit;
  font-size: 13px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

.chunk-highlight {
  padding: 0;
  background: var(--main-100);
  color: inherit;
  cursor: pointer;
}

.chunk-highlight.active {
  background: var(--main-200);
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

.tag-manager {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.tag-display-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tag-input-row {
  display: flex;
  gap: 8px;
}

.no-tags-hint {
  color: var(--color-text-tertiary);
  font-size: 13px;
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
