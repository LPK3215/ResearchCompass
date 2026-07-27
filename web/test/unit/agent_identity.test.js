import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getPreferredAgentId,
  isBuiltinAgent,
  isDefaultBuiltinAgent
} from '../../src/utils/agentIdentity.js'

test('research copilot is selectable but not the default fallback', () => {
  const copilot = { id: 'research-copilot', slug: 'research-copilot' }
  const defaultChatbot = { id: 'default-chatbot', slug: 'default-chatbot' }

  assert.equal(isBuiltinAgent(copilot), true)
  assert.equal(isDefaultBuiltinAgent(copilot), false)
  assert.equal(isDefaultBuiltinAgent(defaultChatbot), true)
  // Without persisted selection, default-chatbot remains the preferred default
  assert.equal(getPreferredAgentId([copilot, defaultChatbot]), 'default-chatbot')
  // Dual-entry coexistence: persisted research-copilot selection is respected
  assert.equal(
    getPreferredAgentId([copilot, defaultChatbot], 'research-copilot'),
    'research-copilot'
  )
})

test('research copilot is never used as the fallback for ordinary chat', () => {
  const copilot = { id: 'research-copilot', slug: 'research-copilot' }
  const customAgent = { id: 'custom-agent', slug: 'custom-agent' }

  assert.equal(getPreferredAgentId([copilot, customAgent]), 'custom-agent')
  assert.equal(getPreferredAgentId([copilot]), null)
})
