<template>
  <section class="synthesis-evidence-panel">
    <header class="synthesis-evidence-header">
      <div class="synthesis-evidence-title">
        <ShieldCheck :size="17" />
        <h3>证据校验</h3>
        <a-tag :color="pendingCount === 0 ? 'green' : 'orange'">
          已验证 {{ verifiedCount }} / {{ totalCount }}
        </a-tag>
      </div>
      <a-button
        type="primary"
        size="small"
        :loading="bulkLoading"
        :disabled="pendingCount === 0"
        @click="verifyAllPending"
      >
        <template #icon><ShieldCheck :size="14" /></template>
        一键录入并验证
      </a-button>
    </header>
    <p class="synthesis-evidence-hint">
      综述只引用「已验证」证据。确认下列片段后录入并验证，再点击右上角「重新生成」。
    </p>
    <a-alert v-if="loadError" type="error" show-icon :message="loadError" />
    <a-empty v-else-if="!groups.length" description="本次运行没有可校验的检索片段" />
    <div v-else class="synthesis-evidence-groups">
      <article v-for="group in groups" :key="group.paper_id" class="synthesis-evidence-group">
        <header class="synthesis-evidence-group-header">
          <span class="synthesis-evidence-paper">{{ group.paper_title }}</span>
          <span class="synthesis-evidence-count">{{ group.chunks.length }} 个片段</span>
        </header>
        <div
          v-for="item in group.chunks"
          :key="item.chunk_id"
          class="synthesis-evidence-item"
        >
          <div class="synthesis-evidence-item-head">
            <span class="synthesis-evidence-section">
              {{ item.section_title || item.section_type || '正文片段' }}
            </span>
            <a-tag :color="statusColor(item.status)">{{ statusLabel(item.status) }}</a-tag>
          </div>
          <p class="synthesis-evidence-content">{{ item.content }}</p>
          <div class="synthesis-evidence-item-actions">
            <a-button
              v-if="!item.evidence_id"
              size="small"
              :loading="item.busy"
              @click="recordAndVerify(item)"
            >
              录入证据
            </a-button>
            <a-button
              v-else-if="item.status === 'unverified'"
              size="small"
              type="primary"
              :loading="item.busy"
              @click="verifyOne(item)"
            >
              验证
            </a-button>
            <span v-else class="synthesis-evidence-note">
              {{ item.status === 'verified' ? '已可用于本次综述' : '该片段已被标记，无法再次验证' }}
            </span>
          </div>
        </div>
      </article>
    </div>
  </section>
</template>

<script setup>
// 综述失败于证据校验时，给出「本次检索命中的片段 → 录入证据 → 验证」的就地修复入口。
// 后端综述只接受 status=verified 的证据，且每个知识片段在库内只能存在一条证据记录，
// 因此这里按 source_chunk_id 对齐既有证据，避免重复录入。
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { ShieldCheck } from 'lucide-vue-next'
import { researchApi } from '@/apis/research_api'

const props = defineProps({
  kbId: { type: String, default: '' },
  snapshot: { type: Object, default: null }
})

const emit = defineEmits(['changed'])

const items = ref([])
const loadError = ref('')
const bulkLoading = ref(false)

const groups = computed(() => {
  const grouped = new Map()
  for (const item of items.value) {
    if (!grouped.has(item.paper_id)) {
      grouped.set(item.paper_id, { paper_id: item.paper_id, paper_title: item.paper_title, chunks: [] })
    }
    grouped.get(item.paper_id).chunks.push(item)
  }
  return [...grouped.values()]
})

const totalCount = computed(() => items.value.length)
const verifiedCount = computed(() => items.value.filter((item) => item.status === 'verified').length)
// 已录入但被标记作废/归档的片段无法再转回已验证，因此不计入待处理数量。
const pendingCount = computed(
  () => items.value.filter((item) => !item.evidence_id || item.status === 'unverified').length
)

const statusLabel = (status) => ({
  unverified: '待验证',
  verified: '已验证',
  invalid: '已作废',
  archived: '已归档'
}[status] || '未录入')

const statusColor = (status) => ({
  unverified: 'orange',
  verified: 'green',
  invalid: 'red'
}[status] || 'default')

const load = async () => {
  if (!props.kbId || !props.snapshot) {
    items.value = []
    return
  }
  loadError.value = ''
  const chunks = []
  for (const paper of props.snapshot.papers || []) {
    for (const chunk of paper.evidence || []) {
      chunks.push({
        chunk_id: chunk.chunk_id,
        paper_id: paper.paper_id,
        paper_title: paper.title,
        section_title: chunk.section_title,
        section_type: chunk.section_type,
        content: chunk.content || '',
        evidence_id: null,
        status: null,
        busy: false
      })
    }
  }
  try {
    const response = await researchApi.listEvidence(props.kbId)
    const records = new Map((response?.items || []).map((row) => [row.source_chunk_id, row]))
    items.value = chunks.map((chunk) => {
      const record = records.get(chunk.chunk_id)
      return record
        ? { ...chunk, evidence_id: record.evidence_id, status: record.status }
        : chunk
    })
  } catch (error) {
    loadError.value = error.message || '证据列表加载失败'
  }
}

const verifyOne = async (item) => {
  if (!item.evidence_id) return
  try {
    const record = await researchApi.transitionEvidence(item.evidence_id, { status: 'verified' })
    item.status = record.status
  } catch (error) {
    message.error(error.message || '证据验证失败')
  }
}

const recordAndVerify = async (item) => {
  item.busy = true
  try {
    const record = await researchApi.createEvidence(props.kbId, { chunk_id: item.chunk_id })
    item.evidence_id = record.evidence_id
    item.status = record.status
    await verifyOne(item)
  } catch (error) {
    message.error(error.message || '证据录入失败')
  } finally {
    item.busy = false
  }
}

const verifyAllPending = async () => {
  bulkLoading.value = true
  let recorded = 0
  let verified = 0
  try {
    for (const item of items.value) {
      if (item.evidence_id && item.status !== 'unverified') continue
      item.busy = true
      try {
        if (!item.evidence_id) {
          const record = await researchApi.createEvidence(props.kbId, { chunk_id: item.chunk_id })
          item.evidence_id = record.evidence_id
          item.status = record.status
          recorded += 1
        }
        if (item.status === 'unverified') {
          const record = await researchApi.transitionEvidence(item.evidence_id, { status: 'verified' })
          item.status = record.status
          verified += 1
        }
      } catch (error) {
        message.error(`片段 ${item.chunk_id} 处理失败：${error.message || '未知错误'}`)
      } finally {
        item.busy = false
      }
    }
    message.success(`已录入 ${recorded} 条、验证 ${verified} 条证据`)
    emit('changed')
  } finally {
    bulkLoading.value = false
  }
}

watch(() => [props.kbId, props.snapshot], load, { immediate: true })
</script>

<style scoped lang="less">
.synthesis-evidence-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.synthesis-evidence-header,
.synthesis-evidence-title,
.synthesis-evidence-group-header,
.synthesis-evidence-item-head {
  display: flex;
  align-items: center;
}

.synthesis-evidence-header {
  justify-content: space-between;
  gap: 12px;
}

.synthesis-evidence-title {
  gap: 8px;
}

.synthesis-evidence-title h3 {
  margin: 0;
  font-size: 15px;
}

.synthesis-evidence-hint {
  margin: 0;
  color: var(--gray-600);
  font-size: 13px;
}

.synthesis-evidence-groups {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.synthesis-evidence-group {
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  overflow: hidden;
}

.synthesis-evidence-group-header {
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px;
  background: var(--gray-25);
}

.synthesis-evidence-paper {
  font-weight: 600;
  font-size: 13px;
}

.synthesis-evidence-count {
  color: var(--gray-500);
  font-size: 12px;
}

.synthesis-evidence-item {
  padding: 10px 12px;
  border-top: 1px solid var(--gray-150);
}

.synthesis-evidence-item-head {
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}

.synthesis-evidence-section {
  color: var(--gray-700);
  font-size: 12px;
}

.synthesis-evidence-content {
  margin: 0 0 8px;
  max-height: 120px;
  overflow-y: auto;
  white-space: pre-wrap;
  color: var(--gray-800);
  font-size: 12px;
  line-height: 1.6;
}

.synthesis-evidence-item-actions {
  display: flex;
  align-items: center;
}

.synthesis-evidence-note {
  color: var(--gray-500);
  font-size: 12px;
}
</style>
