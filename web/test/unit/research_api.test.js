import assert from 'node:assert/strict'
import { beforeEach, mock, test } from 'node:test'

import { moduleMockExportsKey } from './module_mock_compat.js'

const calls = []
let requestResult = { ok: true }

mock.module(new URL('../../src/apis/base.js', import.meta.url), {
  [moduleMockExportsKey]: {
    apiGet: async (...args) => {
      calls.push({ method: 'apiGet', args })
      return requestResult
    },
    apiRequest: async (...args) => {
      calls.push({ method: 'apiRequest', args })
      return requestResult
    }
  }
})

const { publicUserStudyApi, researchApi } = await import('../../src/apis/research_api.js')

beforeEach(() => {
  calls.length = 0
  requestResult = { ok: true }
})

test('ensureCopilotThread posts the current research context as JSON', async () => {
  const payload = {
    kb_id: 'kb-1',
    project_id: 'project-1',
    surface: 'search',
    selection: { type: 'search_run', id: 'run-1', title: 'Graph RAG' }
  }

  await researchApi.ensureCopilotThread(payload)

  assert.deepEqual(calls, [
    {
      method: 'apiRequest',
      args: [
        '/api/research/copilot/thread',
        { method: 'POST', body: JSON.stringify(payload) }
      ]
    }
  ])
})

test('listProjectTemplates requests the authenticated template catalog', async () => {
  await researchApi.listProjectTemplates()

  assert.deepEqual(calls, [
    { method: 'apiGet', args: ['/api/research/project-templates'] }
  ])
})

test('listPapers encodes the knowledge base and omits empty filters', async () => {
  await researchApi.listPapers('kb/team one', {
    page: 2,
    page_size: 20,
    keyword: 'graph RAG',
    year: null,
    tag: ''
  })

  assert.deepEqual(calls, [
    {
      method: 'apiGet',
      args: ['/api/research/databases/kb%2Fteam%20one/papers?page=2&page_size=20&keyword=graph+RAG']
    }
  ])
})

test('searchPapers sends the selected retrieval contract as JSON', async () => {
  const payload = { query: 'evidence grounded RAG', mode: 'strict_graph', top_k: 8 }

  await researchApi.searchPapers('kb-1', payload)

  assert.deepEqual(calls, [
    {
      method: 'apiRequest',
      args: [
        '/api/research/databases/kb-1/search',
        { method: 'POST', body: JSON.stringify(payload) }
      ]
    }
  ])
})

test('research project APIs encode project and asset identifiers', async () => {
  const createPayload = { title: 'Project', research_question: 'Question?' }
  const assetPayload = { asset_type: 'paper', reference_ids: ['paper/1'] }
  await researchApi.createProject('kb/team one', createPayload)
  await researchApi.listProjects('kb/team one', { status: 'active', query: 'graph RAG', offset: 0, limit: 20 })
  await researchApi.getProject('project/1')
  await researchApi.updateProject('project/1', { progress: 60 })
  await researchApi.listProjectAssetCandidates('project/1', { asset_type: 'paper', limit: 20 })
  await researchApi.addProjectAssets('project/1', assetPayload)
  await researchApi.listProjectAssets('project/1', { asset_type: 'paper', offset: 0, limit: 20 })
  await researchApi.updateProjectAsset('project/1', 'asset/1', { notes: 'Important' })
  await researchApi.removeProjectAsset('project/1', 'asset/1')
  await researchApi.deleteProject('project/1')

  assert.deepEqual(calls, [
    {
      method: 'apiRequest',
      args: [
        '/api/research/databases/kb%2Fteam%20one/projects',
        { method: 'POST', body: JSON.stringify(createPayload) }
      ]
    },
    {
      method: 'apiGet',
      args: ['/api/research/databases/kb%2Fteam%20one/projects?status=active&query=graph+RAG&offset=0&limit=20']
    },
    { method: 'apiGet', args: ['/api/research/projects/project%2F1'] },
    {
      method: 'apiRequest',
      args: ['/api/research/projects/project%2F1', { method: 'PATCH', body: JSON.stringify({ progress: 60 }) }]
    },
    {
      method: 'apiGet',
      args: ['/api/research/projects/project%2F1/asset-candidates?asset_type=paper&limit=20']
    },
    {
      method: 'apiRequest',
      args: [
        '/api/research/projects/project%2F1/assets',
        { method: 'POST', body: JSON.stringify(assetPayload) }
      ]
    },
    {
      method: 'apiGet',
      args: ['/api/research/projects/project%2F1/assets?asset_type=paper&offset=0&limit=20']
    },
    {
      method: 'apiRequest',
      args: [
        '/api/research/projects/project%2F1/assets/asset%2F1',
        { method: 'PATCH', body: JSON.stringify({ notes: 'Important' }) }
      ]
    },
    {
      method: 'apiRequest',
      args: ['/api/research/projects/project%2F1/assets/asset%2F1', { method: 'DELETE' }]
    },
    { method: 'apiRequest', args: ['/api/research/projects/project%2F1', { method: 'DELETE' }] }
  ])
})

test('research project risk APIs encode project and risk identifiers', async () => {
  const payload = { title: '风险', description: '描述', severity: 'high', owner_uid: 'u/1', mitigation: '' }
  await researchApi.listProjectRisks('project/1')
  await researchApi.createProjectRisk('project/1', payload)
  await researchApi.transitionProjectRisk('project/1', 'risk/1', { status: 'mitigating' })
  await researchApi.listProjectRiskActivities('project/1', 'risk/1')

  assert.deepEqual(calls, [
    { method: 'apiGet', args: ['/api/research/projects/project%2F1/risks'] },
    {
      method: 'apiRequest',
      args: ['/api/research/projects/project%2F1/risks', { method: 'POST', body: JSON.stringify(payload) }]
    },
    {
      method: 'apiRequest',
      args: [
        '/api/research/projects/project%2F1/risks/risk%2F1/transition',
        { method: 'POST', body: JSON.stringify({ status: 'mitigating' }) }
      ]
    },
    { method: 'apiGet', args: ['/api/research/projects/project%2F1/risks/risk%2F1/activities'] }
  ])
})

test('evidence lifecycle APIs encode identifiers and preserve audit contracts', async () => {
  const createPayload = { title: 'Evidence', source_chunk_id: 'chunk/1' }
  const transitionPayload = { to_status: 'verified', reason: 'Reviewed' }

  await researchApi.listEvidence('kb/team one', {
    status: 'verified', query: 'graph RAG', offset: 0, limit: 20
  })
  await researchApi.createEvidence('kb/team one', createPayload)
  await researchApi.transitionEvidence('evidence/1', transitionPayload)
  await researchApi.listEvidenceActivities('evidence/1')
  await researchApi.listEvidenceCitations('evidence/1')
  await researchApi.listEvidenceImpacts('evidence/1')

  assert.deepEqual(calls, [
    {
      method: 'apiGet',
      args: ['/api/research/databases/kb%2Fteam%20one/evidence?status=verified&query=graph+RAG&offset=0&limit=20']
    },
    {
      method: 'apiRequest',
      args: [
        '/api/research/databases/kb%2Fteam%20one/evidence',
        { method: 'POST', body: JSON.stringify(createPayload) }
      ]
    },
    {
      method: 'apiRequest',
      args: [
        '/api/research/evidence/evidence%2F1/transition',
        { method: 'POST', body: JSON.stringify(transitionPayload) }
      ]
    },
    {
      method: 'apiGet',
      args: ['/api/research/evidence/evidence%2F1/activities']
    },
    {
      method: 'apiGet',
      args: ['/api/research/evidence/evidence%2F1/citations']
    },
    {
      method: 'apiGet',
      args: ['/api/research/evidence/evidence%2F1/impacts']
    }
  ])
})

test('research project execution APIs preserve plan contracts and blob responses', async () => {
  await researchApi.getProjectPlan('project/1')
  await researchApi.createProjectMilestone('project/1', { title: 'Milestone' })
  await researchApi.updateProjectMilestone('project/1', 'milestone/1', { status: 'active' })
  await researchApi.reorderProjectMilestones('project/1', ['milestone/1'])
  await researchApi.createProjectTask('project/1', { title: 'Task', milestone_id: 'milestone/1' })
  await researchApi.updateProjectTask('project/1', 'task/1', { status: 'done' })
  await researchApi.reorderProjectTasks('project/1', {
    milestone_id: 'milestone/1', task_ids: ['task/1']
  })
  await researchApi.createProjectPlanAssetLink('project/1', {
    asset_id: 'asset/1', task_id: 'task/1'
  })
  await researchApi.deleteProjectPlanAssetLink('project/1', 'link/1')
  await researchApi.deleteProjectTask('project/1', 'task/1')
  await researchApi.deleteProjectMilestone('project/1', 'milestone/1')
  await researchApi.exportProjectReport('project/1', 'docx')

  assert.deepEqual(calls, [
    { method: 'apiGet', args: ['/api/research/projects/project%2F1/plan'] },
    {
      method: 'apiRequest',
      args: ['/api/research/projects/project%2F1/milestones', {
        method: 'POST', body: JSON.stringify({ title: 'Milestone' })
      }]
    },
    {
      method: 'apiRequest',
      args: ['/api/research/projects/project%2F1/milestones/milestone%2F1', {
        method: 'PATCH', body: JSON.stringify({ status: 'active' })
      }]
    },
    {
      method: 'apiRequest',
      args: ['/api/research/projects/project%2F1/milestones/order', {
        method: 'PUT', body: JSON.stringify({ milestone_ids: ['milestone/1'] })
      }]
    },
    {
      method: 'apiRequest',
      args: ['/api/research/projects/project%2F1/tasks', {
        method: 'POST', body: JSON.stringify({ title: 'Task', milestone_id: 'milestone/1' })
      }]
    },
    {
      method: 'apiRequest',
      args: ['/api/research/projects/project%2F1/tasks/task%2F1', {
        method: 'PATCH', body: JSON.stringify({ status: 'done' })
      }]
    },
    {
      method: 'apiRequest',
      args: ['/api/research/projects/project%2F1/tasks/order', {
        method: 'PUT',
        body: JSON.stringify({ milestone_id: 'milestone/1', task_ids: ['task/1'] })
      }]
    },
    {
      method: 'apiRequest',
      args: ['/api/research/projects/project%2F1/plan/asset-links', {
        method: 'POST', body: JSON.stringify({ asset_id: 'asset/1', task_id: 'task/1' })
      }]
    },
    {
      method: 'apiRequest',
      args: ['/api/research/projects/project%2F1/plan/asset-links/link%2F1', { method: 'DELETE' }]
    },
    {
      method: 'apiRequest',
      args: ['/api/research/projects/project%2F1/tasks/task%2F1', { method: 'DELETE' }]
    },
    {
      method: 'apiRequest',
      args: ['/api/research/projects/project%2F1/milestones/milestone%2F1', { method: 'DELETE' }]
    },
    {
      method: 'apiRequest',
      args: ['/api/research/projects/project%2F1/report?format=docx', { method: 'GET' }, true, 'blob']
    }
  ])
})

test('research task dependency APIs encode identifiers and preserve mutation contracts', async () => {
  await researchApi.createProjectTaskDependency('project/a', {
    task_id: 'task/1', depends_on_task_id: 'task/0'
  })
  await researchApi.deleteProjectTaskDependency('project/a', 'dependency/1')
  assert.deepEqual(calls.slice(-2), [
    {
      method: 'apiRequest',
      args: ['/api/research/projects/project%2Fa/task-dependencies', {
        method: 'POST', body: JSON.stringify({ task_id: 'task/1', depends_on_task_id: 'task/0' })
      }]
    },
    {
      method: 'apiRequest',
      args: ['/api/research/projects/project%2Fa/task-dependencies/dependency%2F1', { method: 'DELETE' }]
    }
  ])
})

test('search history APIs encode ids and preserve management contracts', async () => {
  await researchApi.listSearchRuns('kb/team one', { offset: 20, limit: 20 })
  await researchApi.getSearchRun('run/1')
  await researchApi.updateSearchRun('run/1', { is_pinned: true })
  await researchApi.deleteSearchRun('run/1')

  assert.deepEqual(calls, [
    {
      method: 'apiGet',
      args: ['/api/research/databases/kb%2Fteam%20one/search-runs?offset=20&limit=20']
    },
    {
      method: 'apiGet',
      args: ['/api/research/search-runs/run%2F1']
    },
    {
      method: 'apiRequest',
      args: [
        '/api/research/search-runs/run%2F1',
        { method: 'PATCH', body: JSON.stringify({ is_pinned: true }) }
      ]
    },
    {
      method: 'apiRequest',
      args: ['/api/research/search-runs/run%2F1', { method: 'DELETE' }]
    }
  ])
})

test('exportResearchSynthesis requests an authenticated blob response', async () => {
  await researchApi.exportResearchSynthesis('run/1', 'docx')

  assert.deepEqual(calls, [
    {
      method: 'apiRequest',
      args: [
        '/api/research/synthesis-runs/run%2F1/export?format=docx',
        { method: 'GET' },
        true,
        'blob'
      ]
    }
  ])
})

test('exportPapersBibtex returns the response blob for selected papers', async () => {
  const expected = new Blob(['@article{research-compass}'])
  requestResult = { blob: async () => expected }

  const result = await researchApi.exportPapersBibtex('kb 1', ['paper/1', 'paper 2'])

  assert.equal(result, expected)
  assert.deepEqual(calls, [
    {
      method: 'apiRequest',
      args: [
        '/api/research/databases/kb%201/papers/export?paper_ids=paper%2F1%2Cpaper+2',
        { method: 'GET' },
        true,
        'blob'
      ]
    }
  ])
})

test('public user study requests never require authentication', async () => {
  await publicUserStudyApi.getStudy('invite-token')
  await publicUserStudyApi.submitResponse('invite-token', { consent: true, overall_rating: 5 })

  assert.deepEqual(calls, [
    {
      method: 'apiRequest',
      args: [
        '/api/research/user-studies/public/resolve',
        { method: 'POST', body: JSON.stringify({ token: 'invite-token' }) },
        false
      ]
    },
    {
      method: 'apiRequest',
      args: [
        '/api/research/user-studies/public/responses',
        {
          method: 'POST',
          body: JSON.stringify({ token: 'invite-token', consent: true, overall_rating: 5 })
        },
        false
      ]
    }
  ])
})

test('exportKbBundle requests an authenticated zip blob', async () => {
  const expected = new Blob(['PK'])
  requestResult = { blob: async () => expected }

  assert.equal(await researchApi.exportKbBundle('kb/1'), expected)
  assert.deepEqual(calls, [
    {
      method: 'apiRequest',
      args: ['/api/research/databases/kb%2F1/export-bundle', { method: 'GET' }, true, 'blob']
    }
  ])
})

test('validateKbManifest posts the manifest payload', async () => {
  const manifest = { papers: [{ paper_id: 'p1', title: 'Paper' }] }

  await researchApi.validateKbManifest('kb/1', manifest)

  assert.deepEqual(calls, [
    {
      method: 'apiRequest',
      args: [
        '/api/research/databases/kb%2F1/export-manifest/validate',
        { method: 'POST', body: JSON.stringify({ manifest }) }
      ]
    }
  ])
})

test('findCrossLibraryDuplicates requests the cross-library report', async () => {
  await researchApi.findCrossLibraryDuplicates()

  assert.deepEqual(calls, [{ method: 'apiGet', args: ['/api/research/duplicates/cross-library'] }])
})

test('paper screening, chat and snowball APIs preserve research contracts', async () => {
  const exportBlob = new Blob(['paper_id,title'])
  await researchApi.getPaperScreening('kb/1', { paper_ids: 'paper/1,paper 2', limit: 200 })
  await researchApi.setPaperScreening('kb/1', {
    paper_ids: ['paper/1'],
    decision: 'included',
    reason: 'relevant'
  })
  requestResult = { blob: async () => exportBlob }
  assert.equal(await researchApi.exportPaperScreening('kb/1'), exportBlob)
  await researchApi.askPaperQuestion('kb/1', 'paper/1', { question: 'What method?', history: [] })
  await researchApi.listRelatedPapers('kb/1', 'paper/1', 'references', 20)
  await researchApi.importRelatedPapers('kb/1', 'paper/1', {
    direction: 'references',
    paper_ids: ['s2-1'],
    include_full_text: false
  })

  assert.deepEqual(calls, [
    {
      method: 'apiGet',
      args: ['/api/research/databases/kb%2F1/papers/screening?paper_ids=paper%2F1%2Cpaper+2&limit=200']
    },
    {
      method: 'apiRequest',
      args: [
        '/api/research/databases/kb%2F1/papers/screening',
        {
          method: 'POST',
          body: JSON.stringify({ paper_ids: ['paper/1'], decision: 'included', reason: 'relevant' })
        }
      ]
    },
    {
      method: 'apiRequest',
      args: [
        '/api/research/databases/kb%2F1/papers/screening/export',
        { method: 'GET' },
        true,
        'blob'
      ]
    },
    {
      method: 'apiRequest',
      args: [
        '/api/research/databases/kb%2F1/papers/paper%2F1/chat',
        { method: 'POST', body: JSON.stringify({ question: 'What method?', history: [] }) }
      ]
    },
    {
      method: 'apiGet',
      args: ['/api/research/databases/kb%2F1/papers/paper%2F1/related?direction=references&limit=20']
    },
    {
      method: 'apiRequest',
      args: [
        '/api/research/databases/kb%2F1/papers/paper%2F1/related/import',
        {
          method: 'POST',
          body: JSON.stringify({
            direction: 'references',
            paper_ids: ['s2-1'],
            include_full_text: false
          })
        }
      ]
    }
  ])
})

test('translatePaper posts the section and limit to the paper endpoint', async () => {
  await researchApi.translatePaper('kb/1', 'paper/1', { section_type: 'methodology', limit: 6 })

  assert.deepEqual(calls, [
    {
      method: 'apiRequest',
      args: [
        '/api/research/databases/kb%2F1/papers/paper%2F1/translate',
        { method: 'POST', body: JSON.stringify({ section_type: 'methodology', limit: 6 }) }
      ]
    }
  ])
})

test('onboarding, metadata enrich, reference import and paper notes APIs preserve contracts', async () => {
  const exportBlob = new Blob(['# notes'])
  const referencePayload = { content: '@article{a, title={T}}', format: 'bibtex' }
  const notePayload = { content: 'note', chunk_id: 'chunk/1', start_offset: 0, end_offset: 4 }

  await researchApi.getOnboardingState()
  await researchApi.enrichPaperMetadata('kb/1', 'paper/1')
  await researchApi.importReferenceFile('kb/1', referencePayload)
  await researchApi.listPaperNotes('kb/1', 'paper/1')
  await researchApi.createPaperNote('kb/1', 'paper/1', notePayload)
  await researchApi.updatePaperNote('kb/1', 'paper/1', 'note/1', 'updated note')
  await researchApi.deletePaperNote('kb/1', 'paper/1', 'note/1')
  requestResult = { blob: async () => exportBlob }
  assert.equal(await researchApi.exportPaperNotes('kb/1', 'paper/1'), exportBlob)

  assert.deepEqual(calls, [
    { method: 'apiGet', args: ['/api/research/onboarding'] },
    {
      method: 'apiRequest',
      args: [
        '/api/research/databases/kb%2F1/papers/paper%2F1/metadata/enrich',
        { method: 'POST' }
      ]
    },
    {
      method: 'apiRequest',
      args: [
        '/api/research/databases/kb%2F1/papers/import-references',
        { method: 'POST', body: JSON.stringify(referencePayload) }
      ]
    },
    { method: 'apiGet', args: ['/api/research/databases/kb%2F1/papers/paper%2F1/notes'] },
    {
      method: 'apiRequest',
      args: [
        '/api/research/databases/kb%2F1/papers/paper%2F1/notes',
        { method: 'POST', body: JSON.stringify(notePayload) }
      ]
    },
    {
      method: 'apiRequest',
      args: [
        '/api/research/databases/kb%2F1/papers/paper%2F1/notes/note%2F1',
        { method: 'PATCH', body: JSON.stringify({ content: 'updated note' }) }
      ]
    },
    {
      method: 'apiRequest',
      args: [
        '/api/research/databases/kb%2F1/papers/paper%2F1/notes/note%2F1',
        { method: 'DELETE' }
      ]
    },
    {
      method: 'apiRequest',
      args: [
        '/api/research/databases/kb%2F1/papers/paper%2F1/notes/export',
        { method: 'GET' },
        true,
        'blob'
      ]
    }
  ])
})

test('duplicate merge and manifest export APIs preserve contracts', async () => {
  const exportBlob = new Blob(['{}'])
  await researchApi.mergeDuplicateGroup('kb/1', 'doi:10.1/x')
  requestResult = { blob: async () => exportBlob }
  assert.equal(await researchApi.exportKbManifest('kb/1'), exportBlob)

  assert.deepEqual(calls, [
    {
      method: 'apiRequest',
      args: [
        '/api/research/databases/kb%2F1/duplicates/merge',
        { method: 'POST', body: JSON.stringify({ key: 'doi:10.1/x' }) }
      ]
    },
    {
      method: 'apiRequest',
      args: [
        '/api/research/databases/kb%2F1/export-manifest',
        { method: 'GET' },
        true,
        'blob'
      ]
    }
  ])
})
