// Built during the Meta Global AI Developer Hackathon (new work).
// Headless-Chrome check of the DINOv2 wiring on the local stack (web 5180, backend 8781; a verified test user on the Plus plan
// who has imported fixture reels). Screenshots at desktop (1280) and mobile (390) widths, plus assertions on what is shown.
// Needs web/node_modules (playwright-core). Credentials come from the environment, never from the command line output.
// Usage: E2E_EMAIL=... E2E_PASSWORD=... node scripts/screenshot_dino_wire.mjs <analysis result.json> <out folder> [attention.json]
//   attention.json (optional): replaces result.viewerAttention, e.g. the output of wire.analyse_video on a clip with a held end frame.
import { createRequire } from 'module'
import fs from 'fs'
const require = createRequire(new URL('../web/', import.meta.url).pathname)
const { chromium } = require('playwright-core')
const [, , RESULT, OUT, ATTENTION] = process.argv
fs.mkdirSync(OUT, { recursive: true })
const BASE = process.env.E2E_BASE || 'http://localhost:5180'
const API = process.env.E2E_API || 'http://127.0.0.1:8781'
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
let failed = 0
const check = (name, ok, extra = '') => { if (!ok) failed++; console.log(ok ? 'PASS' : 'FAIL', name, extra) }

const login = await (await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json', 'user-agent': UA },
  body: JSON.stringify({ email: process.env.E2E_EMAIL, password: process.env.E2E_PASSWORD }) })).json()
const result = JSON.parse(fs.readFileSync(RESULT, 'utf8')).result
if (ATTENTION) result.viewerAttention = JSON.parse(fs.readFileSync(ATTENTION, 'utf8'))

const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true })
for (const [label, vp] of [['desktop', { width: 1280, height: 900 }], ['mobile', { width: 390, height: 844 }]]) {
  const ctx = await browser.newContext({ userAgent: UA, viewport: vp, deviceScaleFactor: label === 'mobile' ? 2 : 1 })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(String(e)))
  await page.goto(`${BASE}/login`)
  await page.evaluate(([a, r, res]) => {
    localStorage.setItem('prysm_token', a); localStorage.setItem('prysm_refresh', r)
    localStorage.setItem('prysm_result', JSON.stringify(res)); localStorage.setItem('prysm_video_source', 'none')
  }, [login.access_token, login.refresh_token, result])
  await page.goto(`${BASE}/results`)
  await page.waitForSelector('[data-testid=attention-strip]', { timeout: 20000 })
  await page.waitForSelector('[data-testid=closest-reel]', { timeout: 20000 })

  // --- viewer card: the attention strip
  const strip = page.locator('[data-testid=attention-strip]')
  await strip.scrollIntoViewIfNeeded()
  const stripText = await strip.textContent()
  check(`${label}: attention strip says estimate`, (await strip.getByText('estimate', { exact: true }).count()) >= 3)
  check(`${label}: attention strip shows no percentages`, !stripText.includes('%'))
  check(`${label}: three rows (visual change, audio energy, audio change)`, (await strip.locator('[data-testid^=row-]').count()) === 3)
  check(`${label}: no invented prediction in the strip`, !/retention|will watch|predict/i.test(stripText))
  await page.locator('[data-testid=viewer-behaviour]').screenshot({ path: `${OUT}/${label}-viewer-card.png` })
  const marks = { still: await strip.locator('[data-testid=mark-still]').count(), quiet: await strip.locator('[data-testid=mark-quiet]').count() }
  console.log(`INFO ${label}: still marks=${marks.still} quiet marks=${marks.quiet}`)
  if (ATTENTION) check(`${label}: still stretch and quiet dip are marked`, marks.still >= 1 && marks.quiet >= 1)

  // --- comparison card: closest reel
  const closest = page.locator('[data-testid=closest-reel]')
  await closest.scrollIntoViewIfNeeded()
  const head = await page.textContent('[data-testid=closest-headline]')
  check(`${label}: headline names the closest reel`, /Closest to your Reel \d+/.test(head), head.replace(/\s+/g, ' ').trim())
  check(`${label}: shows that reel's real numbers in words`, /Reached [\d,]+ accounts|Had [\d,]+ views|likes|watched on average/.test(await page.textContent('[data-testid=closest-list]')))
  check(`${label}: honest note present`, /pictures, not results/.test(await page.textContent('[data-testid=closest-honest-note]')))
  const small = await page.locator('[data-testid=closest-small-sample]').count()
  console.log(`INFO ${label}: small-sample note shown=${small === 1}`)
  const card = page.locator('[data-testid=creator-dna-card]')
  await ((await card.count()) ? card : closest).screenshot({ path: `${OUT}/${label}-comparison-card.png` })
  // the new cards must fit the viewport; the page-level number is reported too (a hidden tooltip elsewhere on /results already sticks out by 9 px on mobile)
  const cardOverflow = await page.evaluate(() => [...document.querySelectorAll('[data-testid=viewer-behaviour] *, [data-testid=closest-reel] *, [data-testid=creator-dna-card] *')]
    .filter(e => e.getBoundingClientRect().right > window.innerWidth + 1).length)
  check(`${label}: nothing in the new cards sticks out of the viewport`, cardOverflow === 0, `elements=${cardOverflow}`)
  console.log(`INFO ${label}: page-level overflow=${await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)}px`)
  await page.screenshot({ path: `${OUT}/${label}-results-full.png`, fullPage: true })

  // --- Creator DNA page: picture matching card
  await page.goto(`${BASE}/creator-dna`)
  await page.waitForSelector('[data-testid=picture-matching]', { timeout: 20000 })
  const pm = page.locator('[data-testid=picture-matching]')
  await pm.scrollIntoViewIfNeeded()
  console.log(`INFO ${label}: picture matching: ${(await pm.locator('h2').textContent()).trim()}`)
  if (process.env.E2E_BACKFILL === '1' && label === 'desktop') {
    await page.click('[data-testid=backfill-button]')
    await page.waitForSelector('[data-testid=backfill-result]', { timeout: 120000 })
    console.log('INFO backfill:', (await page.textContent('[data-testid=backfill-result]')).trim())
    check('backfill finished and all reels can be compared', /Done:/.test(await page.textContent('[data-testid=backfill-result]')))
  }
  await pm.screenshot({ path: `${OUT}/${label}-picture-matching.png` })
  const o2 = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  check(`${label}: creator-dna page no horizontal scroll`, o2 <= 1, `overflow=${o2}`)
  check(`${label}: no page errors`, errors.length === 0, errors.join(' | ').slice(0, 200))
  await ctx.close()
}
await browser.close()
console.log(failed ? `${failed} FAILED` : 'ALL PASSED')
process.exit(failed ? 1 : 0)
