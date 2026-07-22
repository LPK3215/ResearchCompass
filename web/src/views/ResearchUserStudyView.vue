<template>
  <main class="study-page">
    <section class="study-card">
      <template v-if="loading">
        <a-skeleton active :paragraph="{ rows: 8 }" />
      </template>
      <a-result v-else-if="loadError" status="error" title="无法打开用户评测" :sub-title="loadError" />
      <a-result v-else-if="submitted" status="success" title="感谢您的参与" sub-title="您的匿名反馈已提交，不需要再进行任何操作。" />
      <template v-else-if="study">
        <header>
          <span class="eyebrow">ResearchCompass 用户评测</span>
          <h1>{{ study.name }}</h1>
          <p>{{ study.description || '感谢您参与科研罗盘的匿名用户体验评测。' }}</p>
        </header>

        <a-alert class="consent" type="info" show-icon message="匿名参与说明" :description="study.consent_text" />
        <a-checkbox v-model:checked="form.consent">我已阅读并同意以上匿名参与说明</a-checkbox>

        <section class="form-section">
          <h2>参与者背景</h2>
          <div class="form-grid">
            <a-form-item label="当前研究阶段" required>
              <a-select v-model:value="form.research_stage">
                <a-select-option value="undergraduate">本科阶段</a-select-option>
                <a-select-option value="master">硕士阶段</a-select-option>
                <a-select-option value="doctoral">博士阶段</a-select-option>
                <a-select-option value="other">其他</a-select-option>
              </a-select>
            </a-form-item>
            <a-form-item label="科研经验" required>
              <a-select v-model:value="form.research_experience">
                <a-select-option value="none">暂无科研经验</a-select-option>
                <a-select-option value="under_1_year">不足 1 年</a-select-option>
                <a-select-option value="1_to_3_years">1–3 年</a-select-option>
                <a-select-option value="over_3_years">3 年以上</a-select-option>
              </a-select>
            </a-form-item>
          </div>
        </section>

        <section class="form-section">
          <h2>任务体验</h2>
          <p>请在实际试用检索、论文分析、引用溯源和趋势分析后，按 1（非常困难/无帮助）到 5（非常容易/非常有帮助）评分。</p>
          <a-form-item v-for="item in taskQuestions" :key="item.key" :label="item.label" required>
            <a-radio-group v-model:value="form.task_scores[item.key]" class="score-group">
              <a-radio v-for="score in [1, 2, 3, 4, 5]" :key="score" :value="score">{{ score }}</a-radio>
            </a-radio-group>
          </a-form-item>
        </section>

        <section class="form-section">
          <h2>系统可用性量表（SUS）</h2>
          <p>请按 1（非常不同意）到 5（非常同意）评分。</p>
          <a-form-item v-for="item in susQuestions" :key="item.key" :label="item.label" required>
            <a-radio-group v-model:value="form.sus_scores[item.key]" class="score-group">
              <a-radio v-for="score in [1, 2, 3, 4, 5]" :key="score" :value="score">{{ score }}</a-radio>
            </a-radio-group>
          </a-form-item>
        </section>

        <section class="form-section">
          <h2>整体反馈</h2>
          <a-form-item label="总体满意度（1–5）" required>
            <a-radio-group v-model:value="form.overall_rating" class="score-group"><a-radio v-for="score in [1, 2, 3, 4, 5]" :key="score" :value="score">{{ score }}</a-radio></a-radio-group>
          </a-form-item>
          <a-form-item label="向同学推荐本系统的意愿（0–10）" required>
            <a-radio-group v-model:value="form.recommend_score" class="score-group"><a-radio v-for="score in recommendScores" :key="score" :value="score">{{ score }}</a-radio></a-radio-group>
          </a-form-item>
          <a-form-item label="哪些功能最有帮助？还有哪些需要改进？">
            <a-textarea v-model:value="form.feedback" :rows="4" :maxlength="4000" show-count placeholder="可选；请不要填写姓名、学号、联系方式等个人信息。" />
          </a-form-item>
        </section>
        <a-button type="primary" block size="large" :loading="submitting" @click="submit">提交匿名问卷</a-button>
      </template>
    </section>
  </main>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { publicUserStudyApi } from '@/apis/research_api'

const token = new URLSearchParams(String(window.location.hash || '').slice(1)).get('token') || ''
const loading = ref(true)
const submitting = ref(false)
const loadError = ref('')
const study = ref(null)
const submitted = ref(false)
const recommendScores = Array.from({ length: 11 }, (_, index) => index)
const taskQuestions = [
  { key: 'search', label: '我能高效找到与研究问题相关的论文和证据。' },
  { key: 'paper_analysis', label: '论文分析报告帮助我更快理解论文的贡献与局限。' },
  { key: 'citation_traceability', label: '引用溯源让我能够核查系统结论的原始证据。' },
  { key: 'trend_insight', label: '趋势分析帮助我理解研究方向的演进。' }
]
const susQuestions = [
  '我愿意经常使用这个系统。', '我觉得这个系统不必要地复杂。', '我觉得这个系统很容易使用。',
  '我认为需要技术人员支持才能使用这个系统。', '我觉得系统的各项功能整合得很好。', '我觉得系统存在太多不一致之处。',
  '我认为大多数人能很快学会使用这个系统。', '我觉得这个系统使用起来很笨拙。', '我使用这个系统时很有信心。',
  '我需要先学习很多东西才能使用这个系统。'
].map((label, index) => ({ key: `q${index + 1}`, label }))
const form = reactive({
  consent: false, research_stage: undefined, research_experience: undefined,
  task_scores: Object.fromEntries(taskQuestions.map((item) => [item.key, undefined])),
  sus_scores: Object.fromEntries(susQuestions.map((item) => [item.key, undefined])),
  overall_rating: undefined, recommend_score: undefined, feedback: ''
})

const validate = () => {
  if (!form.consent || !form.research_stage || !form.research_experience) return '请确认参与说明并完成背景信息'
  if (Object.values(form.task_scores).some((value) => !Number.isInteger(value))) return '请完成全部任务体验评分'
  if (Object.values(form.sus_scores).some((value) => !Number.isInteger(value))) return '请完成全部 SUS 评分'
  if (!Number.isInteger(form.overall_rating) || !Number.isInteger(form.recommend_score)) return '请完成整体反馈评分'
  return ''
}

const submit = async () => {
  const validation = validate()
  if (validation) return message.warning(validation)
  submitting.value = true
  try {
    await publicUserStudyApi.submitResponse(token, { ...form })
    submitted.value = true
  } catch (error) {
    message.error(error.message || '提交失败，请检查链接是否已使用')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  if (!token) { loadError.value = '评测链接无效或缺少参与凭证'; loading.value = false; return }
  try { study.value = await publicUserStudyApi.getStudy(token) } catch (error) { loadError.value = error.message || '评测链接无效或已关闭' } finally { loading.value = false }
})
</script>

<style scoped lang="less">
.study-page { min-height: 100vh; padding: 40px 20px; background: var(--gray-50); }
.study-card { width: min(880px, 100%); margin: 0 auto; padding: 36px; background: var(--gray-0); border: 1px solid var(--gray-200); border-radius: 12px;
  header { margin-bottom: 24px; } h1 { margin: 6px 0; color: var(--gray-900); } p { color: var(--gray-600); line-height: 1.7; }
}
.eyebrow { color: var(--primary-600); font-weight: 600; font-size: 13px; }.consent { margin-bottom: 14px; }
.form-section { margin-top: 34px; padding-top: 24px; border-top: 1px solid var(--gray-200); h2 { margin: 0 0 8px; color: var(--gray-900); font-size: 18px; } }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }.score-group { display: flex; flex-wrap: wrap; gap: 12px; }
@media (max-width: 640px) { .study-page { padding: 16px; }.study-card { padding: 22px; }.form-grid { grid-template-columns: 1fr; } }
</style>
