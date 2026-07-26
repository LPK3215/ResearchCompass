import assert from 'node:assert/strict'
import { mock, test } from 'node:test'

const calls = []

const record = (method) => async (...args) => {
  calls.push({ method, args })
  return { ok: true }
}

mock.module(new URL('../../src/apis/base.js', import.meta.url), {
  exports: {
    apiGet: record('apiGet'),
    apiAdminGet: record('apiAdminGet'),
    apiAdminPost: record('apiAdminPost'),
    apiAdminPut: record('apiAdminPut'),
    apiAdminDelete: record('apiAdminDelete'),
    apiRequest: record('apiRequest')
  }
})

const { evaluationApi } = await import('../../src/apis/knowledge_api.js')

test('downloadCorpusManifest requests an authenticated blob with an encoded knowledge base id', async () => {
  await evaluationApi.downloadCorpusManifest('kb/team one')

  assert.deepEqual(calls, [
    {
      method: 'apiAdminGet',
      args: ['/api/evaluation/databases/kb%2Fteam%20one/corpus-manifest', {}, 'blob']
    }
  ])
})
