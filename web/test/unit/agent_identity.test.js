import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getPreferredAgentId,
  isBuiltinAgent,
  isDefaultBuiltinAgent
} from '../../src/utils/agentIdentity.js'

test('research copilot is the default agent for the conversation page', () => {
  const copilot = { id: 'research-copilot', slug: 'research-copilot' }
  const defaultChatbot = { id: 'default-chatbot', slug: 'default-chatbot' }

  assert.equal(isBuiltinAgent(copilot), true)
  assert.equal(isDefaultBuiltinAgent(copilot), false)
  assert.equal(isDefaultBuiltinAgent(defaultChatbot), true)
  // 无持久化选择时，默认使用 research-copilot
  assert.equal(getPreferredAgentId([copilot, defaultChatbot]), 'research-copilot')
  // 持久化选择被尊重
  assert.equal(
    getPreferredAgentId([copilot, defaultChatbot], 'default-chatbot'),
    'default-chatbot'
  )
  assert.equal(
    getPreferredAgentId([copilot, defaultChatbot], 'research-copilot'),
    'research-copilot'
  )
})

test('falls back to default-chatbot when research-copilot is absent', () => {
  const defaultChatbot = { id: 'default-chatbot', slug: 'default-chatbot' }
  const customAgent = { id: 'custom-agent', slug: 'custom-agent' }

  assert.equal(getPreferredAgentId([defaultChatbot, customAgent]), 'default-chatbot')
})

test('falls back to custom agent when no builtin agent exists', () => {
  const customAgent = { id: 'custom-agent', slug: 'custom-agent' }

  assert.equal(getPreferredAgentId([customAgent]), 'custom-agent')
  assert.equal(getPreferredAgentId([]), null)
})
