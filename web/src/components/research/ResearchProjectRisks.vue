<template>
  <section class="risk-panel">
    <header class="section-heading"><div><h3>风险登记</h3><span>{{ risks.length }} 项</span></div><a-button data-testid="create-research-risk" type="primary" :disabled="readOnly" @click="openCreate">登记风险</a-button></header>
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-empty v-else-if="!loading && !risks.length" description="暂无登记风险" />
    <a-list v-else :data-source="risks" bordered>
      <template #renderItem="{ item }"><a-list-item><div class="risk-row"><div><strong>{{ item.title }}</strong><p>{{ item.description }}</p><small>{{ severityLabel(item.severity) }} · {{ item.owner_uid }} · {{ statusLabel(item.status) }}</small></div><a-select v-if="!readOnly" :data-testid="`research-risk-status-${item.risk_id}`" :value="item.status" :options="statusOptions" size="small" @change="status => transition(item, status)" /></div></a-list-item></template>
    </a-list>
    <a-modal v-model:open="modalOpen" title="登记研究风险" ok-text="保存" cancel-text="取消" :confirm-loading="saving" @ok="create">
      <a-form layout="vertical"><a-form-item label="标题" required><a-input data-testid="research-risk-title" v-model:value="form.title" /></a-form-item><a-form-item label="描述" required><a-textarea data-testid="research-risk-description" v-model:value="form.description" :rows="3" /></a-form-item><a-form-item label="等级"><a-select v-model:value="form.severity" :options="severityOptions" /></a-form-item><a-form-item label="责任人 UID" required><a-input data-testid="research-risk-owner" v-model:value="form.owner_uid" /></a-form-item><a-form-item label="缓解措施"><a-textarea data-testid="research-risk-mitigation" v-model:value="form.mitigation" :rows="3" /></a-form-item></a-form>
    </a-modal>
  </section>
</template>
<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { researchApi } from '@/apis/research_api'
const props = defineProps({ project: { type: Object, required: true } })
const risks = ref([]); const loading = ref(false); const error = ref(''); const modalOpen = ref(false); const saving = ref(false)
const form = reactive({ title: '', description: '', severity: 'medium', owner_uid: '', mitigation: '' })
const severityOptions = [{ value: 'low', label: '低' }, { value: 'medium', label: '中' }, { value: 'high', label: '高' }, { value: 'critical', label: '严重' }]
const statusOptions = [{ value: 'open', label: '开放' }, { value: 'mitigating', label: '处理中' }, { value: 'accepted', label: '已接受' }, { value: 'resolved', label: '已解决' }, { value: 'closed', label: '已关闭' }]
const readOnly = computed(() => props.project.status !== 'active')
const severityLabel = value => severityOptions.find(item => item.value === value)?.label || value
const statusLabel = value => statusOptions.find(item => item.value === value)?.label || value
const load = async () => { loading.value = true; error.value = ''; try { risks.value = (await researchApi.listProjectRisks(props.project.project_id)).items || [] } catch (err) { error.value = err.message || '风险加载失败' } finally { loading.value = false } }
const openCreate = () => { Object.assign(form, { title: '', description: '', severity: 'medium', owner_uid: '', mitigation: '' }); modalOpen.value = true }
const create = async () => { if (!form.title.trim() || !form.description.trim() || !form.owner_uid.trim()) { message.warning('请填写风险标题、描述和责任人'); return } saving.value = true; try { await researchApi.createProjectRisk(props.project.project_id, { ...form }); modalOpen.value = false; await load(); message.success('风险已登记') } catch (err) { message.error(err.message || '风险登记失败') } finally { saving.value = false } }
const transition = async (risk, status) => { try { await researchApi.transitionProjectRisk(props.project.project_id, risk.risk_id, { status }); await load() } catch (err) { message.error(err.message || '风险状态更新失败'); await load() } }
watch(() => props.project.project_id, load); onMounted(load)
</script>
<style scoped>
.risk-panel { padding: 24px; }.risk-row { display: flex; justify-content: space-between; gap: 16px; width: 100%; }.risk-row p { margin: 5px 0; color: var(--color-text-secondary); }.risk-row small { color: var(--color-text-tertiary); }
</style>
