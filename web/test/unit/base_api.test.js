import assert from 'node:assert/strict'
import { afterEach, mock, test } from 'node:test'

import { moduleMockExportsKey } from './module_mock_compat.js'

mock.module(new URL('../../src/stores/user.js', import.meta.url), {
  [moduleMockExportsKey]: {
    checkAdminPermission: () => {},
    checkSuperAdminPermission: () => {},
    useUserStore: () => ({
      isLoggedIn: true,
      getAuthHeaders: () => ({ Authorization: 'Bearer test-token' })
    })
  }
})

mock.module('ant-design-vue', {
  [moduleMockExportsKey]: {
    message: { error: () => {} }
  }
})

const { apiRequest } = await import('../../src/apis/base.js')
const originalFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = originalFetch
})

test('apiRequest resolves successful 204 JSON responses without parsing an empty body', async () => {
  globalThis.fetch = async () => new Response(null, {
    status: 204,
    headers: { 'Content-Type': 'application/json' }
  })

  const result = await apiRequest('/api/research/projects/project-1', { method: 'DELETE' })

  assert.equal(result, null)
})

test('apiRequest retries an idempotent GET once after a transient network failure', async () => {
  let calls = 0
  globalThis.fetch = async () => {
    calls += 1
    if (calls === 1) throw new TypeError('Failed to fetch')
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    })
  }

  const result = await apiRequest('/api/system/health')

  assert.deepEqual(result, { ok: true })
  assert.equal(calls, 2)
})

test('apiRequest does not retry a failed POST', async () => {
  let calls = 0
  globalThis.fetch = async () => {
    calls += 1
    throw new TypeError('Failed to fetch')
  }

  await assert.rejects(() => apiRequest('/api/research/projects', { method: 'POST', body: '{}' }))
  assert.equal(calls, 1)
})
