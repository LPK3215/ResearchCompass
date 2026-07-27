import assert from 'node:assert/strict'
import test from 'node:test'

import {
  AGENT_ENTRY_PATH,
  getProductEntryRedirect,
  isLiteModeEnabled,
  resolveProductEntry
} from '../../src/utils/productEntry.js'

test('lite mode accepts supported boolean environment values', () => {
  for (const value of [true, 'true', 'TRUE', '1']) {
    assert.equal(isLiteModeEnabled(value), true)
  }

  for (const value of [false, undefined, null, '', 'false', '0', 'yes']) {
    assert.equal(isLiteModeEnabled(value), false)
  }
})

test('product entry always resolves to the agent conversation page', () => {
  assert.equal(resolveProductEntry(false), AGENT_ENTRY_PATH)
  assert.equal(resolveProductEntry(true), AGENT_ENTRY_PATH)
})

test('lite mode redirects only ResearchCompass product paths', () => {
  assert.equal(getProductEntryRedirect('/research', true), AGENT_ENTRY_PATH)
  assert.equal(getProductEntryRedirect('/research/knowledge-base', true), AGENT_ENTRY_PATH)
  assert.equal(getProductEntryRedirect('/research-study', true), null)
  assert.equal(getProductEntryRedirect('/agent', true), null)
  assert.equal(getProductEntryRedirect('/agent/thread-id', true), null)
  assert.equal(getProductEntryRedirect('/research', false), null)
})
