import { apiGet, apiRequest } from './base.js'

const buildQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      query.set(key, String(value))
    }
  })
  return query.toString()
}

export const researchApi = {
  ensureCopilotThread: (payload) =>
    apiRequest('/api/research/copilot/thread', {
      method: 'POST', body: JSON.stringify(payload)
    }),

  createProject: (kbId, payload) =>
    apiRequest(`/api/research/databases/${encodeURIComponent(kbId)}/projects`, {
      method: 'POST', body: JSON.stringify(payload)
    }),

  listProjects: (kbId, params = {}) => {
    const query = buildQuery(params)
    return apiGet(
      `/api/research/databases/${encodeURIComponent(kbId)}/projects${query ? `?${query}` : ''}`
    )
  },

  getProject: (projectId) =>
    apiGet(`/api/research/projects/${encodeURIComponent(projectId)}`),

  getProjectPlan: (projectId) =>
    apiGet(`/api/research/projects/${encodeURIComponent(projectId)}/plan`),

  createProjectMilestone: (projectId, payload) =>
    apiRequest(`/api/research/projects/${encodeURIComponent(projectId)}/milestones`, {
      method: 'POST', body: JSON.stringify(payload)
    }),

  updateProjectMilestone: (projectId, milestoneId, payload) =>
    apiRequest(
      `/api/research/projects/${encodeURIComponent(projectId)}/milestones/${encodeURIComponent(milestoneId)}`,
      { method: 'PATCH', body: JSON.stringify(payload) }
    ),

  deleteProjectMilestone: (projectId, milestoneId) =>
    apiRequest(
      `/api/research/projects/${encodeURIComponent(projectId)}/milestones/${encodeURIComponent(milestoneId)}`,
      { method: 'DELETE' }
    ),

  reorderProjectMilestones: (projectId, milestoneIds) =>
    apiRequest(`/api/research/projects/${encodeURIComponent(projectId)}/milestones/order`, {
      method: 'PUT', body: JSON.stringify({ milestone_ids: milestoneIds })
    }),

  createProjectTask: (projectId, payload) =>
    apiRequest(`/api/research/projects/${encodeURIComponent(projectId)}/tasks`, {
      method: 'POST', body: JSON.stringify(payload)
    }),

  updateProjectTask: (projectId, taskId, payload) =>
    apiRequest(
      `/api/research/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}`,
      { method: 'PATCH', body: JSON.stringify(payload) }
    ),

  deleteProjectTask: (projectId, taskId) =>
    apiRequest(
      `/api/research/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}`,
      { method: 'DELETE' }
    ),

  reorderProjectTasks: (projectId, payload) =>
    apiRequest(`/api/research/projects/${encodeURIComponent(projectId)}/tasks/order`, {
      method: 'PUT', body: JSON.stringify(payload)
    }),

  createProjectPlanAssetLink: (projectId, payload) =>
    apiRequest(`/api/research/projects/${encodeURIComponent(projectId)}/plan/asset-links`, {
      method: 'POST', body: JSON.stringify(payload)
    }),

  deleteProjectPlanAssetLink: (projectId, linkId) =>
    apiRequest(
      `/api/research/projects/${encodeURIComponent(projectId)}/plan/asset-links/${encodeURIComponent(linkId)}`,
      { method: 'DELETE' }
    ),

  exportProjectReport: (projectId, format = 'markdown') => {
    const query = buildQuery({ format })
    return apiRequest(
      `/api/research/projects/${encodeURIComponent(projectId)}/report?${query}`,
      { method: 'GET' }, true, 'blob'
    )
  },

  updateProject: (projectId, payload) =>
    apiRequest(`/api/research/projects/${encodeURIComponent(projectId)}`, {
      method: 'PATCH', body: JSON.stringify(payload)
    }),

  deleteProject: (projectId) =>
    apiRequest(`/api/research/projects/${encodeURIComponent(projectId)}`, { method: 'DELETE' }),

  listProjectAssetCandidates: (projectId, params = {}) => {
    const query = buildQuery(params)
    return apiGet(
      `/api/research/projects/${encodeURIComponent(projectId)}/asset-candidates${query ? `?${query}` : ''}`
    )
  },

  addProjectAssets: (projectId, payload) =>
    apiRequest(`/api/research/projects/${encodeURIComponent(projectId)}/assets`, {
      method: 'POST', body: JSON.stringify(payload)
    }),

  listProjectAssets: (projectId, params = {}) => {
    const query = buildQuery(params)
    return apiGet(
      `/api/research/projects/${encodeURIComponent(projectId)}/assets${query ? `?${query}` : ''}`
    )
  },

  updateProjectAsset: (projectId, assetId, payload) =>
    apiRequest(
      `/api/research/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(assetId)}`,
      { method: 'PATCH', body: JSON.stringify(payload) }
    ),

  removeProjectAsset: (projectId, assetId) =>
    apiRequest(
      `/api/research/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(assetId)}`,
      { method: 'DELETE' }
    ),

  listPapers: (kbId, params = {}) => {
    const query = buildQuery(params)
    return apiGet(`/api/research/databases/${encodeURIComponent(kbId)}/papers${query ? `?${query}` : ''}`)
  },

  getPaper: (kbId, paperId) =>
    apiGet(
      `/api/research/databases/${encodeURIComponent(kbId)}/papers/${encodeURIComponent(paperId)}`
    ),

  listPaperChunks: (kbId, paperId, params = {}) => {
    const query = buildQuery(params)
    return apiGet(
      `/api/research/databases/${encodeURIComponent(kbId)}/papers/${encodeURIComponent(paperId)}/chunks${query ? `?${query}` : ''}`
    )
  },

  getPaperEvidence: (kbId, paperId, chunkId) =>
    apiGet(
      `/api/research/databases/${encodeURIComponent(kbId)}/papers/${encodeURIComponent(paperId)}/evidence/${encodeURIComponent(chunkId)}`
    ),

  analyzePaper: (kbId, paperId, payload = {}) =>
    apiRequest(
      `/api/research/databases/${encodeURIComponent(kbId)}/papers/${encodeURIComponent(paperId)}/analysis`,
      { method: 'POST', body: JSON.stringify(payload) }
    ),

  getPaperAnalysisRun: (runId) =>
    apiGet(`/api/research/paper-analysis-runs/${encodeURIComponent(runId)}`),

  getLatestPaperAnalysis: (kbId, paperId) =>
    apiGet(
      `/api/research/databases/${encodeURIComponent(kbId)}/papers/${encodeURIComponent(paperId)}/analysis/latest`
    ),

  createPaperAnalysisEvaluation: (kbId, payload) =>
    apiRequest(
      `/api/research/databases/${encodeURIComponent(kbId)}/paper-analysis-evaluations`,
      { method: 'POST', body: JSON.stringify(payload) }
    ),

  listPaperAnalysisEvaluations: (kbId) =>
    apiGet(`/api/research/databases/${encodeURIComponent(kbId)}/paper-analysis-evaluations`),

  getPaperAnalysisEvaluation: (kbId, evaluationId) =>
    apiGet(
      `/api/research/databases/${encodeURIComponent(kbId)}/paper-analysis-evaluations/${encodeURIComponent(evaluationId)}`
    ),

  listPaperAnalysisBlindItems: (kbId, evaluationId) =>
    apiGet(
      `/api/research/databases/${encodeURIComponent(kbId)}/paper-analysis-evaluations/${encodeURIComponent(evaluationId)}/blind-items`
    ),

  getPaperAnalysisBlindItem: (kbId, evaluationId, itemId) =>
    apiGet(
      `/api/research/databases/${encodeURIComponent(kbId)}/paper-analysis-evaluations/${encodeURIComponent(evaluationId)}/blind-items/${encodeURIComponent(itemId)}`
    ),

  submitPaperAnalysisBlindScore: (kbId, evaluationId, itemId, payload) =>
    apiRequest(
      `/api/research/databases/${encodeURIComponent(kbId)}/paper-analysis-evaluations/${encodeURIComponent(evaluationId)}/blind-items/${encodeURIComponent(itemId)}/scores`,
      { method: 'POST', body: JSON.stringify(payload) }
    ),

  updatePaper: (kbId, paperId, payload) =>
    apiRequest(
      `/api/research/databases/${encodeURIComponent(kbId)}/papers/${encodeURIComponent(paperId)}`,
      { method: 'PATCH', body: JSON.stringify(payload) }
    ),

  searchPapers: (kbId, payload) =>
    apiRequest(
      `/api/research/databases/${encodeURIComponent(kbId)}/search`,
      { method: 'POST', body: JSON.stringify(payload) }
    ),

  getSearchRun: (runId) => apiGet(`/api/research/search-runs/${encodeURIComponent(runId)}`),

  listSearchRuns: (kbId, params = {}) => {
    const query = buildQuery(params)
    return apiGet(
      `/api/research/databases/${encodeURIComponent(kbId)}/search-runs${query ? `?${query}` : ''}`
    )
  },

  updateSearchRun: (runId, payload) =>
    apiRequest(
      `/api/research/search-runs/${encodeURIComponent(runId)}`,
      { method: 'PATCH', body: JSON.stringify(payload) }
    ),

  deleteSearchRun: (runId) =>
    apiRequest(
      `/api/research/search-runs/${encodeURIComponent(runId)}`,
      { method: 'DELETE' }
    ),

  createResearchSynthesis: (kbId, payload) =>
    apiRequest(
      `/api/research/databases/${encodeURIComponent(kbId)}/syntheses`,
      { method: 'POST', body: JSON.stringify(payload) }
    ),

  listResearchSyntheses: (kbId, params = {}) => {
    const query = buildQuery(params)
    return apiGet(
      `/api/research/databases/${encodeURIComponent(kbId)}/syntheses${query ? `?${query}` : ''}`
    )
  },

  getResearchSynthesisRun: (runId) =>
    apiGet(`/api/research/synthesis-runs/${encodeURIComponent(runId)}`),

  cancelResearchSynthesis: (runId) =>
    apiRequest(
      `/api/research/synthesis-runs/${encodeURIComponent(runId)}/cancel`,
      { method: 'POST', body: JSON.stringify({}) }
    ),

  regenerateResearchSynthesis: (runId) =>
    apiRequest(
      `/api/research/synthesis-runs/${encodeURIComponent(runId)}/regenerate`,
      { method: 'POST', body: JSON.stringify({}) }
    ),

  exportResearchSynthesis: (runId, format = 'markdown') => {
    const query = buildQuery({ format })
    return apiRequest(
      `/api/research/synthesis-runs/${encodeURIComponent(runId)}/export?${query}`,
      { method: 'GET' }, true, 'blob'
    )
  },

  syncAcademicGraph: (kbId, payload) =>
    apiRequest(
      `/api/research/databases/${encodeURIComponent(kbId)}/academic-graph/sync`,
      { method: 'POST', body: JSON.stringify(payload) }
    ),

  getAcademicGraphSyncRun: (runId) =>
    apiGet(`/api/research/academic-graph/sync-runs/${encodeURIComponent(runId)}`),

  listAcademicGraphConflicts: (runId, params = {}) => {
    const query = buildQuery(params)
    return apiGet(
      `/api/research/academic-graph/sync-runs/${encodeURIComponent(runId)}/conflicts${query ? `?${query}` : ''}`
    )
  },

  getAcademicGraph: (kbId, params = {}) => {
    const query = buildQuery(params)
    return apiGet(
      `/api/research/databases/${encodeURIComponent(kbId)}/academic-graph${query ? `?${query}` : ''}`
    )
  },

  getAcademicGraphRelations: (kbId, graphPaperId, params = {}) => {
    const query = buildQuery(params)
    return apiGet(
      `/api/research/databases/${encodeURIComponent(kbId)}/academic-graph/relations/${encodeURIComponent(graphPaperId)}${query ? `?${query}` : ''}`
    )
  },

  searchExternalPapers: (kbId, query, limit = 10) => {
    const queryString = buildQuery({ query, limit })
    return apiGet(
      `/api/research/databases/${encodeURIComponent(kbId)}/external-papers/search?${queryString}`
    )
  },

  importExternalPaper: (kbId, identifier) =>
    apiRequest(
      `/api/research/databases/${encodeURIComponent(kbId)}/external-papers/import`,
      { method: 'POST', body: JSON.stringify({ identifier }) }
    ),

  getAcademicTrends: (kbId, params = {}) => {
    const query = buildQuery(params)
    return apiGet(
      `/api/research/databases/${encodeURIComponent(kbId)}/trends${query ? `?${query}` : ''}`
    )
  },

  getAcademicOpportunities: (kbId, params = {}) => {
    const query = buildQuery(params)
    return apiGet(
      `/api/research/databases/${encodeURIComponent(kbId)}/opportunities${query ? `?${query}` : ''}`
    )
  },

  createUserStudy: (kbId, payload) =>
    apiRequest(`/api/research/databases/${encodeURIComponent(kbId)}/user-studies`, {
      method: 'POST', body: JSON.stringify(payload)
    }),

  listUserStudies: (kbId) =>
    apiGet(`/api/research/databases/${encodeURIComponent(kbId)}/user-studies`),

  getUserStudy: (kbId, studyId, params = {}) => {
    const query = buildQuery(params)
    return apiGet(
      `/api/research/databases/${encodeURIComponent(kbId)}/user-studies/${encodeURIComponent(studyId)}${query ? `?${query}` : ''}`
    )
  },

  closeUserStudy: (kbId, studyId) =>
    apiRequest(`/api/research/databases/${encodeURIComponent(kbId)}/user-studies/${encodeURIComponent(studyId)}/close`, {
      method: 'POST', body: JSON.stringify({})
    }),

  exportUserStudy: (kbId, studyId) =>
    apiRequest(
      `/api/research/databases/${encodeURIComponent(kbId)}/user-studies/${encodeURIComponent(studyId)}/export`,
      { method: 'GET' }, true, 'blob'
    ),

  exportPapersBibtex: (kbId, paperIds = null) => {
    const query = buildQuery(paperIds ? { paper_ids: paperIds.join(',') } : {})
    return apiRequest(
      `/api/research/databases/${encodeURIComponent(kbId)}/papers/export${query ? `?${query}` : ''}`,
      { method: 'GET' }, true, 'blob'
    ).then((response) => response.blob())
  },

  listUserTags: (kbId) =>
    apiGet(`/api/research/databases/${encodeURIComponent(kbId)}/papers/tags`),

  listPaperTags: (kbId, paperId) =>
    apiGet(`/api/research/databases/${encodeURIComponent(kbId)}/papers/${encodeURIComponent(paperId)}/tags`),

  addPaperTag: (kbId, paperId, tag) =>
    apiRequest(
      `/api/research/databases/${encodeURIComponent(kbId)}/papers/${encodeURIComponent(paperId)}/tags`,
      { method: 'POST', body: JSON.stringify({ tag }) }
    ),

  removePaperTag: (kbId, paperId, tag) => {
    const query = buildQuery({ tag })
    return apiRequest(
      `/api/research/databases/${encodeURIComponent(kbId)}/papers/${encodeURIComponent(paperId)}/tags?${query}`,
      { method: 'DELETE' }
    )
  }
}

export const publicUserStudyApi = {
  getStudy: (token) => apiRequest(
    '/api/research/user-studies/public/resolve',
    { method: 'POST', body: JSON.stringify({ token }) }, false
  ),
  submitResponse: (token, payload) => apiRequest(
    '/api/research/user-studies/public/responses',
    { method: 'POST', body: JSON.stringify({ token, ...payload }) }, false
  )
}

export default researchApi
