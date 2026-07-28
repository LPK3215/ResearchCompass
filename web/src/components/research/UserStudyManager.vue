<template>
  <div class="study-manager">
    <div class="manager-header">
      <div><h3>研究生用户评测</h3><p>用匿名单次链接收集真实试用反馈；系统不收集身份信息。</p></div>
      <a-button type="primary" class="lucide-icon-btn" @click="openCreate"><Plus :size="16" /> 新建评测</a-button>
    </div>
    <a-alert type="info" show-icon message="真实评测要求" description="建议创建 3–5 个链接，邀请不同研究生完成真实任务后填写。没有响应时系统不会显示或推断结果。" />
    <a-table :loading="loading" :data-source="studies" :columns="columns" row-key="study_id" :pagination="false" class="study-table">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'progress'">{{ record.submitted_count }}/{{ record.participant_count }}</template>
        <template v-else-if="column.key === 'status'"><a-tag :color="record.status === 'open' ? 'processing' : 'default'">{{ record.status === 'open' ? '收集中' : '已关闭' }}</a-tag></template>
        <template v-else-if="column.key === 'actions'"><a-space><a @click="viewReport(record)">报告</a><a v-if="record.status === 'open'" @click="closeStudy(record)">关闭</a></a-space></template>
      </template>
    </a-table>

    <a-modal v-model:open="createVisible" title="创建匿名用户评测" :confirm-loading="creating" @ok="createStudy">
      <a-form layout="vertical"><a-form-item label="评测名称" required><a-input v-model:value="createForm.name" :maxlength="255" /></a-form-item>
        <a-form-item label="试用任务说明"><a-textarea v-model:value="createForm.description" :rows="3" :maxlength="4000" placeholder="例如：完成一项文献检索、查看一篇论文分析报告，并核查一条引用证据。" /></a-form-item>
        <a-form-item label="参与者数量" required><a-input-number v-model:value="createForm.participant_count" :min="3" :max="20" /></a-form-item>
      </a-form><a-alert type="info" show-icon message="创建后将只显示一次明文链接" description="请立即复制并发送给参与者；数据库只保存不可逆 token 哈希。" />
    </a-modal>

    <a-modal v-model:open="inviteVisible" title="匿名评测链接" :footer="null" width="760px" @afterClose="clearCreatedInvites"><p>请复制以下链接发送给参与者。关闭后无法再次显示相同 token。</p>
      <div v-for="(invite, index) in createdInvites" :key="invite.invite_id" class="invite-row"><span>参与者 {{ index + 1 }}</span><a-input :value="publicLink(invite.token)" readonly><template #addonAfter><a @click="copy(publicLink(invite.token))">复制</a></template></a-input></div>
    </a-modal>

    <a-modal v-model:open="reportVisible" :title="`评测报告 · ${report?.name || ''}`" :footer="null" width="960px"><a-spin :spinning="reportLoading"><template v-if="report">
      <div class="report-summary"><article><span>有效响应</span><strong>{{ report.summary.response_count }}/{{ report.participant_count }}</strong></article><article><span>SUS 平均分</span><strong>{{ report.summary.sus_mean ?? '-' }}</strong></article><article><span>总体满意度</span><strong>{{ format(report.summary.overall_rating_mean) }}</strong></article><article><span>推荐意愿</span><strong>{{ report.summary.recommend_score_mean ?? '-' }}/10</strong></article></div>
      <a-table :data-source="taskRows" :columns="taskColumns" :pagination="false" row-key="key" size="small" /><div class="report-actions"><a-button :disabled="!report.summary.response_count" @click="downloadReport">导出匿名原始响应 CSV</a-button></div>
      <section v-if="report.responses.length" class="feedback-section"><h4>匿名文字反馈</h4><a-empty v-if="!feedbacks.length" description="参与者未填写文字反馈" /><a-list v-else :data-source="feedbacks" size="small"><template #renderItem="{ item }"><a-list-item>{{ item }}</a-list-item></template></a-list></section>
    </template></a-spin></a-modal>
  </div>
</template>

<script setup>
// ResearchCompass 用户评测管理器
// 本组件是本仓库在开源智能体框架 Yuxi 之上实现的科研业务界面，负责创建匿名用户
// 评测、生成一次性参与链接、查看 SUS / 任务评分 / 文字反馈报告与导出匿名原始
// 响应；不收集参与者身份信息，未使用的链接可关闭作废。通用表格、模态框与请求
// 基础设施由 Yuxi 提供。
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Plus } from 'lucide-vue-next'
import { researchApi } from '@/apis/research_api'

const props = defineProps({ kbId: { type: String, required: true } })
const loading = ref(false), creating = ref(false), createVisible = ref(false), inviteVisible = ref(false), reportVisible = ref(false), reportLoading = ref(false)
const studies = ref([]), createdInvites = ref([]), report = ref(null)
const createForm = reactive({ name: '科研罗盘研究生用户评测', description: '', participant_count: 5 })
const columns = [{ title: '评测', dataIndex: 'name', key: 'name' }, { title: '响应', key: 'progress', width: 90 }, { title: '状态', key: 'status', width: 90 }, { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 185 }, { title: '操作', key: 'actions', width: 120 }]
const taskLabels = { search: '文献检索', paper_analysis: '论文分析', citation_traceability: '引用溯源', trend_insight: '趋势分析' }
const taskColumns = [{ title: '任务', dataIndex: 'name', key: 'name' }, { title: '平均评分（1–5）', dataIndex: 'score', key: 'score' }]
const taskRows = computed(() => Object.entries(taskLabels).map(([key, name]) => ({ key, name, score: format(report.value?.summary.task_score_means?.[key]) })))
const feedbacks = computed(() => (report.value?.responses || []).map((item) => item.feedback).filter(Boolean))
const format = (value) => Number.isFinite(value) ? value.toFixed(2) : '-'
const publicLink = (token) => `${window.location.origin}/research-study#token=${encodeURIComponent(token)}`
const copy = async (value) => { try { await navigator.clipboard.writeText(value); message.success('链接已复制') } catch { message.error('复制失败，请手动复制') } }
let kbGeneration = 0
let loadGeneration = 0
let reportGeneration = 0

const clearCreatedInvites = () => { createdInvites.value = [] }
const load = async () => {
  const requestGeneration = ++loadGeneration
  const kbId = props.kbId
  loading.value = true
  try {
    const result = await researchApi.listUserStudies(kbId)
    if (requestGeneration === loadGeneration && kbId === props.kbId) studies.value = result
  } catch (error) {
    if (requestGeneration === loadGeneration && kbId === props.kbId) message.error(error.message || '加载用户评测失败')
  } finally {
    if (requestGeneration === loadGeneration) loading.value = false
  }
}
const openCreate = () => { createVisible.value = true }
const createStudy = async () => {
  if (!createForm.name.trim()) return message.warning('请填写评测名称')
  const requestGeneration = kbGeneration
  const kbId = props.kbId
  creating.value = true
  try {
    const result = await researchApi.createUserStudy(kbId, {
      ...createForm,
      name: createForm.name.trim(),
      description: createForm.description.trim()
    })
    if (requestGeneration !== kbGeneration || kbId !== props.kbId) return
    createdInvites.value = result.invites || []
    createVisible.value = false
    inviteVisible.value = true
    await load()
  } catch (error) {
    if (requestGeneration === kbGeneration && kbId === props.kbId) message.error(error.message || '创建用户评测失败')
  } finally {
    creating.value = false
  }
}
const viewReport = async (study) => {
  const requestGeneration = ++reportGeneration
  const kbId = props.kbId
  reportVisible.value = true
  reportLoading.value = true
  report.value = null
  try {
    const result = await researchApi.getUserStudy(kbId, study.study_id)
    if (requestGeneration === reportGeneration && kbId === props.kbId) report.value = result
  } catch (error) {
    if (requestGeneration === reportGeneration && kbId === props.kbId) message.error(error.message || '加载评测报告失败')
  } finally {
    if (requestGeneration === reportGeneration) reportLoading.value = false
  }
}
const closeStudy = (study) => {
  const kbId = props.kbId
  Modal.confirm({
    title: '关闭用户评测？',
    content: '关闭后未使用的匿名链接将不能再提交。',
    onOk: async () => {
      try {
        await researchApi.closeUserStudy(kbId, study.study_id)
        if (kbId !== props.kbId) return
        await load()
        if (report.value?.study_id === study.study_id) await viewReport(study)
      } catch (error) {
        if (kbId === props.kbId) message.error(error.message || '关闭失败')
      }
    }
  })
}
const downloadReport = async () => {
  const kbId = props.kbId
  const studyId = report.value?.study_id
  if (!studyId) return
  try {
    const response = await researchApi.exportUserStudy(kbId, studyId)
    if (kbId !== props.kbId) return
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${studyId}-anonymous-responses.csv`
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    if (kbId === props.kbId) message.error(error.message || '导出失败')
  }
}

watch(() => props.kbId, () => {
  kbGeneration += 1
  loadGeneration += 1
  reportGeneration += 1
  loading.value = false
  reportLoading.value = false
  createVisible.value = false
  inviteVisible.value = false
  reportVisible.value = false
  report.value = null
  studies.value = []
  clearCreatedInvites()
  load()
})

onBeforeUnmount(() => {
  kbGeneration += 1
  loadGeneration += 1
  reportGeneration += 1
  clearCreatedInvites()
})
onMounted(load)
</script>

<style scoped lang="less">
.study-manager { display: flex; flex-direction: column; gap: 16px; }.manager-header { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; h3 { margin: 0; color: var(--gray-900); } p { margin: 6px 0 0; color: var(--gray-500); } }.study-table { margin-top: 4px; }.invite-row { display: grid; grid-template-columns: 80px 1fr; gap: 12px; align-items: center; margin: 10px 0; }.report-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 18px; article { padding: 14px; border: 1px solid var(--gray-200); border-radius: 8px; span { display: block; color: var(--gray-500); font-size: 12px; } strong { display: block; margin-top: 5px; color: var(--gray-900); font-size: 20px; } } }.report-actions { margin: 16px 0; }.feedback-section { margin-top: 20px; h4 { color: var(--gray-900); } } @media (max-width: 640px) { .manager-header { flex-direction: column; }.invite-row { grid-template-columns: 1fr; }.report-summary { grid-template-columns: repeat(2, 1fr); } }
</style>
