import assert from 'node:assert/strict'
import { beforeEach, mock, test } from 'node:test'

const calls = []
let requestResult = { ok: true }

mock.module(new URL('../../src/apis/base.js', import.meta.url), {
  exports: {
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
