import assert from 'node:assert/strict'
import { beforeEach, mock, test } from 'node:test'

import { moduleMockExportsKey } from './module_mock_compat.js'

const calls = []

mock.module(new URL('../../src/apis/base.js', import.meta.url), {
  [moduleMockExportsKey]: {
    apiGet: async (...args) => {
      calls.push({ method: 'apiGet', args })
      return { items: [] }
    },
    apiRequest: async (...args) => {
      calls.push({ method: 'apiRequest', args })
      return { ok: true }
    }
  }
})

const { notificationApi } = await import('../../src/apis/notification_api.js')

beforeEach(() => calls.length = 0)

test('list serializes notification filters into the query string', async () => {
  await notificationApi.list({ unread_only: true, limit: 10, empty: '' })

  assert.deepEqual(calls, [
    {
      method: 'apiGet',
      args: ['/api/notifications?unread_only=true&limit=10']
    }
  ])
})

test('markRead encodes notification ids before building the path', async () => {
  await notificationApi.markRead('id/with special')

  assert.deepEqual(calls, [
    {
      method: 'apiRequest',
      args: ['/api/notifications/id%2Fwith%20special/read', { method: 'POST' }]
    }
  ])
})
