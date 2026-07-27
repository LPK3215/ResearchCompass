export const AGENT_ENTRY_PATH = '/agent'
export const RESEARCH_ENTRY_PATH = '/research'

export const isLiteModeEnabled = (value) => {
  const normalized = String(value ?? '').trim().toLowerCase()
  return normalized === 'true' || normalized === '1'
}

export const resolveProductEntry = (liteMode) =>
  liteMode ? AGENT_ENTRY_PATH : RESEARCH_ENTRY_PATH

export const isResearchProductPath = (path) =>
  path === RESEARCH_ENTRY_PATH || path.startsWith(`${RESEARCH_ENTRY_PATH}/`)

export const getProductEntryRedirect = (path, liteMode) =>
  liteMode && isResearchProductPath(path) ? AGENT_ENTRY_PATH : null

export const IS_LITE_MODE = isLiteModeEnabled(import.meta.env?.VITE_LITE_MODE)
export const DEFAULT_PRODUCT_ENTRY = resolveProductEntry(IS_LITE_MODE)
