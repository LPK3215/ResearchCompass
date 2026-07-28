import assert from 'node:assert/strict'
import test from 'node:test'

import { isPasswordLongEnough, MIN_PASSWORD_LENGTH } from '../../src/utils/passwordValidation.js'

test('MIN_PASSWORD_LENGTH is 8', () => {
  assert.equal(MIN_PASSWORD_LENGTH, 8)
})

test('isPasswordLongEnough validates minimum length', () => {
  assert.equal(isPasswordLongEnough('1234567'), false)
  assert.equal(isPasswordLongEnough('12345678'), true)
})
