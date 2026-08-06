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
