/**
 * ResearchCompass README banner generator.
 *
 * Purpose: produce the English wide banner used at the top of README.md and
 * README.zh-CN.md. The banner is brand artwork, not a data chart; it uses the
 * same compass motif and main palette as docs/public/favicon.svg.
 *
 * Dependencies: Node.js built-in modules only (fs, path, url). No build step.
 * Run: node scripts/visualization/generate_banner.mjs
 * Output: docs/assets/banner.svg
 * Source of truth: backend/pyproject.toml (license and requires-python).
 */

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '../..')
const pyprojectPath = path.join(root, 'backend/pyproject.toml')
const pyproject = fs.readFileSync(pyprojectPath, 'utf8')

const license = (pyproject.match(/license\s*=\s*\{\s*text\s*=\s*"([^"]+)"/) || [])[1] || 'MIT'
const requiresPython = (pyproject.match(/requires-python\s*=\s*"([^"]+)"/) || [])[1] || '>=3.12'
const pythonRange = requiresPython.match(/>=(\d+)\.(\d+),<(\d+)\.(\d+)/)
const pythonLabel = pythonRange
  ? `Python ${pythonRange[1]}.${pythonRange[2]}\u2013${pythonRange[3]}.${Number(pythonRange[4]) - 1}`
  : `Python ${requiresPython}`

const outputPath = path.join(root, 'docs/assets/banner.svg')
fs.mkdirSync(path.dirname(outputPath), { recursive: true })

const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 360" width="1600" height="360" role="img" aria-labelledby="banner-title banner-desc">
  <title id="banner-title">ResearchCompass</title>
  <desc id="banner-desc">Evidence-traceable research workspace: library, retrieval, evidence, synthesis, project, report.</desc>
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#01151f"/>
      <stop offset="0.55" stop-color="#023944"/>
      <stop offset="1" stop-color="#035065"/>
    </linearGradient>
    <radialGradient id="glowA" cx="0.22" cy="0.5" r="0.55">
      <stop offset="0" stop-color="#046a82" stop-opacity="0.55"/>
      <stop offset="1" stop-color="#046a82" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="glowB" cx="0.84" cy="0.45" r="0.45">
      <stop offset="0" stop-color="#3996ae" stop-opacity="0.36"/>
      <stop offset="1" stop-color="#3996ae" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="accent" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#82c3d6"/>
      <stop offset="1" stop-color="#e1f6fb"/>
    </linearGradient>
    <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
      <path d="M40 0H0V40" fill="none" stroke="#5faec2" stroke-opacity="0.08" stroke-width="1"/>
    </pattern>
  </defs>

  <rect width="1600" height="360" fill="url(#bg)"/>
  <rect width="1600" height="360" fill="url(#grid)"/>
  <circle cx="360" cy="180" r="280" fill="url(#glowA)"/>
  <circle cx="1340" cy="150" r="280" fill="url(#glowB)"/>

  <g font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif">
    <rect x="80" y="60" width="238" height="30" rx="15" fill="none" stroke="#3996ae" stroke-opacity="0.55"/>
    <circle cx="99" cy="75" r="4" fill="#82c3d6"/>
    <text x="114" y="80" font-size="12" font-weight="700" letter-spacing="3" fill="#82c3d6">RESEARCH WORKSPACE</text>

    <text x="78" y="174" font-size="82" font-weight="800" letter-spacing="-2">
      <tspan fill="#e1f6fb">Research</tspan><tspan fill="#82c3d6">Compass</tspan>
    </text>
    <text x="82" y="220" font-size="25" font-weight="500" fill="#c4eaf5">Every sentence traceable to a real paper.</text>

    <g font-size="13" font-weight="700" letter-spacing="1.8" fill="#a3d8e8">
      <text x="82" y="292">LIBRARY</text>
      <text x="232" y="292">RETRIEVAL</text>
      <text x="382" y="292">EVIDENCE</text>
      <text x="532" y="292">SYNTHESIS</text>
      <text x="700" y="292">PROJECT</text>
      <text x="850" y="292">REPORT</text>
    </g>
    <g stroke="#3996ae" stroke-opacity="0.65" stroke-width="1">
      <path d="M166 287h36"/>
      <path d="M318 287h34"/>
      <path d="M462 287h36"/>
      <path d="M625 287h36"/>
      <path d="M770 287h38"/>
    </g>
    <g fill="#3996ae">
      <circle cx="166" cy="287" r="2.5"/><circle cx="202" cy="287" r="2.5"/>
      <circle cx="318" cy="287" r="2.5"/><circle cx="352" cy="287" r="2.5"/>
      <circle cx="462" cy="287" r="2.5"/><circle cx="498" cy="287" r="2.5"/>
      <circle cx="625" cy="287" r="2.5"/><circle cx="661" cy="287" r="2.5"/>
      <circle cx="770" cy="287" r="2.5"/><circle cx="808" cy="287" r="2.5"/>
    </g>

    <text x="82" y="330" font-size="14" fill="#5faec2">${license} · ${pythonLabel} · Built on Yuxi</text>
  </g>

  <g transform="translate(1300 180)">
    <circle r="132" fill="#023944" fill-opacity="0.55" stroke="#3996ae" stroke-opacity="0.75" stroke-width="2"/>
    <circle r="114" fill="none" stroke="#82c3d6" stroke-opacity="0.38" stroke-width="1"/>
    <circle r="94" fill="none" stroke="#5faec2" stroke-opacity="0.3" stroke-width="1" stroke-dasharray="3 7"/>

    <g stroke="#c4eaf5" stroke-width="2.5" stroke-linecap="round" stroke-opacity="0.9">
      <path d="M0-128v-10"/><path d="M0 128v10"/>
      <path d="M-128 0h-10"/><path d="M128 0h10"/>
    </g>
    <g stroke="#82c3d6" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.7">
      <path d="M-90-90l-8-8"/><path d="M90 90l8 8"/>
      <path d="M90-90l8-8"/><path d="M-90 90l-8 8"/>
    </g>

    <g font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif" font-size="12" font-weight="700" letter-spacing="2" fill="#c4eaf5" text-anchor="middle">
      <text x="0" y="-146">N</text>
      <text x="146" y="5">E</text>
      <text x="0" y="160">S</text>
      <text x="-146" y="5">W</text>
    </g>

    <g>
      <polygon points="0,-82 15,0 0,14 -15,0" fill="#ff4d4f"/>
      <polygon points="0,-82 -15,0 0,14 15,0" fill="#ff8082"/>
      <polygon points="0,82 15,0 0,-14 -15,0" fill="#ffffff"/>
      <polygon points="0,82 -15,0 0,-14 15,0" fill="#e4e6e6"/>
    </g>
    <circle r="10" fill="#023944" stroke="#e1f6fb" stroke-width="2"/>
    <circle r="4" fill="#82c3d6"/>

    <g stroke="#3996ae" stroke-opacity="0.55" stroke-width="1">
      <path d="M-158-72L-104-46"/>
      <path d="M158-72L104-46"/>
      <path d="M158 72L104 46"/>
      <path d="M-158 72L-104 46"/>
    </g>
    <g font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif" font-size="12" font-weight="700" letter-spacing="1.5" fill="#a3d8e8">
      <circle cx="-158" cy="-72" r="4" fill="#82c3d6"/>
      <text x="-146" y="-66">PAPER</text>
      <circle cx="158" cy="-72" r="4" fill="#82c3d6"/>
      <text x="118" y="-66">EVIDENCE</text>
      <circle cx="158" cy="72" r="4" fill="#82c3d6"/>
      <text x="128" y="78">CLAIM</text>
      <circle cx="-158" cy="72" r="4" fill="#82c3d6"/>
      <text x="-146" y="78">REPORT</text>
    </g>
  </g>
</svg>
`

fs.writeFileSync(outputPath, svg, 'utf8')
console.log(`wrote ${path.relative(root, outputPath)} (${license}, ${pythonLabel})`)
