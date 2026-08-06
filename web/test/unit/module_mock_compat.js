import process from 'node:process'

const [nodeMajor, nodeMinor] = process.versions.node.split('.').map(Number)

// Node 24.15 replaced namedExports with exports while keeping the old field deprecated.
export const moduleMockExportsKey =
  nodeMajor > 24 || (nodeMajor === 24 && nodeMinor >= 15) ? 'exports' : 'namedExports'
