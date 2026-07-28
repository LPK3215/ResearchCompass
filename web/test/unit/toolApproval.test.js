import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildToolApprovalDecisions,
  hasPendingInterruptPayload,
  isThreadWaitingForUserAction,
  isToolApprovalMode
} from '../../src/utils/toolApproval.js'

test('isToolApprovalMode accepts default and always_trust', () => {
  assert.equal(isToolApprovalMode('default'), true)
  assert.equal(isToolApprovalMode('always_trust'), true)
  assert.equal(isToolApprovalMode('unknown'), false)
})

test('buildToolApprovalDecisions maps approve and reject', () => {
  assert.deepEqual(buildToolApprovalDecisions({ 0: 'approve', 1: 'reject' }, 2), [
    { type: 'approve' },
    { type: 'reject', message: '用户拒绝执行该操作' }
  ])
})

test('hasPendingInterruptPayload detects question and tool_approval payloads', () => {
  assert.equal(hasPendingInterruptPayload({ kind: 'question', questions: [{}] }), true)
  assert.equal(hasPendingInterruptPayload({ kind: 'tool_approval', actionRequests: [{}] }), true)
  assert.equal(hasPendingInterruptPayload({ kind: 'tool_approval', actionRequests: [] }), false)
})

test('isThreadWaitingForUserAction detects interrupted and question states', () => {
  assert.equal(
    isThreadWaitingForUserAction({
      pendingInterrupt: { kind: 'question', questions: [{ id: 'q-1' }] }
    }),
    true
  )
  assert.equal(
    isThreadWaitingForUserAction({ queueSnapshot: { status: 'interrupted' } }),
    true
  )
  assert.equal(
    isThreadWaitingForUserAction({
      pendingInterrupt: null,
      queueSnapshot: { status: 'paused' }
    }),
    false
  )
})
