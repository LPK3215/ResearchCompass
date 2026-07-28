import assert from 'node:assert/strict'
import test from 'node:test'

import {
  AVATAR_BACKGROUND_TOKENS,
  generatePixelAvatar,
  getAvatarColorIndex,
  getAvatarFallbackStyle,
  getAvatarInitials
} from '../../src/utils/pixelAvatar.js'

const DICEBEAR_GLYPHS_AVATAR_BASE_URL = 'https://api.dicebear.com/10.x/glyphs/svg'

test('generatePixelAvatar returns stable output for the same id', () => {
  const first = generatePixelAvatar('user-001')
  const second = generatePixelAvatar('user-001')
  assert.equal(first, second, 'Same ID should generate the same avatar')
})

test('generatePixelAvatar returns different avatars for different ids', () => {
  const first = generatePixelAvatar('user-001')
  const second = generatePixelAvatar('user-002')
  assert.notEqual(first, second, 'Different IDs should generate different avatars')
})

test('generatePixelAvatar returns a DiceBear glyphs avatar URL', () => {
  const avatar = generatePixelAvatar('user-003')
  assert.equal(
    avatar,
    `${DICEBEAR_GLYPHS_AVATAR_BASE_URL}?seed=user-003`,
    'Should return a DiceBear glyphs avatar URL'
  )
})

test('generatePixelAvatar trims and URL-encodes the seed', () => {
  const avatar = generatePixelAvatar(' user/中文 ')
  assert.equal(
    avatar,
    `${DICEBEAR_GLYPHS_AVATAR_BASE_URL}?seed=user%2F%E4%B8%AD%E6%96%87`,
    'Seed should be trimmed and URL encoded'
  )
})

test('generatePixelAvatar rejects empty or null id', () => {
  assert.throws(
    () => generatePixelAvatar(''),
    /requires an id/,
    'Empty ID should be treated as invalid data'
  )
  assert.throws(
    () => generatePixelAvatar(null),
    /requires an id/,
    'Null ID should be treated as invalid data'
  )
})

test('getAvatarInitials returns localized initials', () => {
  assert.equal(getAvatarInitials('张三丰', 'user'), '张三', 'Chinese initials use first two chars')
  assert.equal(getAvatarInitials('Alice', 'user'), 'Al', 'ASCII initials use first two chars')
  assert.equal(getAvatarInitials('', 'user'), '用户', 'User fallback should be localized')
  assert.equal(getAvatarInitials('', 'agent'), '智能', 'Agent fallback should be localized')
})

test('getAvatarColorIndex selects stable fallback color', () => {
  const first = getAvatarColorIndex('user-001')
  const second = getAvatarColorIndex('user-001')
  const third = getAvatarColorIndex('user-002')
  assert.equal(first, second, 'Same seed should select the same fallback color')
  assert.notEqual(first, third, 'Different seeds should be able to select different colors')
  assert.ok(first >= 0 && first < AVATAR_BACKGROUND_TOKENS.length, 'Color index should be valid')
})

test('getAvatarFallbackStyle returns background and color', () => {
  const style = getAvatarFallbackStyle('agent-001')
  assert.equal(typeof style.background, 'string', 'Fallback style should include background')
  assert.equal(typeof style.color, 'string', 'Fallback style should include text color')
})
