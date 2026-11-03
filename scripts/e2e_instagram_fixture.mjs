// Built during the Meta Global AI Developer Hackathon (new work).
// Headless-Chrome check of the whole Creator DNA flow in fixture mode (local stack: web 5177, backend 8778, INSTAGRAM_FIXTURE=1, a verified
// test user ig.tester@example.org). Needs web/node_modules (playwright-core). Usage: node scripts/e2e_instagram_fixture.mjs <analysis result.json>
import { createRequire } from 'module'
import fs from 'fs'
const require = createRequire(new URL('../web/', import.meta.url).pathname)
const { chromium } = require('playwright-core')
const OUT = process.env.E2E_SHOTS || '/tmp/ig-e2e-shots'
fs.mkdirSync(OUT, { recursive: true })
const BASE = 'http://localhost:5177'
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
const results = []
const check = (name, ok, extra = '') => { results.push([ok, name, extra]); console.log(ok ? 'PASS' : 'FAIL', name, extra) }

const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true })
const ctx = await browser.newContext({ userAgent: UA, viewport: { width: 1100, height: 900 } })
const page = await ctx.newPage()
const consoleErrors = []
page.on('pageerror', e => consoleErrors.push(String(e)))

// log in through the API, then hand the tokens to the page like the login screen does
const login = await (await fetch(`${BASE}/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json', 'user-agent': UA },
  body: JSON.stringify({ email: 'ig.tester@example.org', password: 'Str0ng!Passw0rd' }) })).json()
await page.goto(`${BASE}/login`)
await page.evaluate(([a, r]) => { localStorage.setItem('prysm_token', a); localStorage.setItem('prysm_refresh', r) }, [login.access_token, login.refresh_token])
const api = (path, method = 'GET', body) => fetch(`${BASE}${path}`, { method, headers: { authorization: `Bearer ${login.access_token}`, 'content-type': 'application/json', 'user-agent': UA }, body: body ? JSON.stringify(body) : undefined })

// 0. start clean
await api('/instagram/disconnect', 'DELETE')

// 1. consent screen
await page.goto(`${BASE}/creator-dna`)
await page.waitForSelector('[data-testid=consent]')
const consent = await page.textContent('[data-testid=consent]')
check('consent: says what is read', /own reels/.test(consent) && /insights/.test(consent))
check('consent: never stores videos', /never store your videos/.test(consent))
check('consent: deleted on disconnect', /Disconnect removes/.test(consent))
check('consent: AI provider processes frames', /AI model provider/.test(consent) && /frames/.test(consent))
check('consent: names the two scopes only', /instagram_business_basic, instagram_business_manage_insights/.test(consent))
await page.screenshot({ path: `${OUT}/01-consent.png`, fullPage: true })

// 2. connect (fixture)
await page.click('[data-testid=connect]')
await page.waitForSelector('[data-testid=import-card]')
check('fixture banner shown', await page.isVisible('[data-testid=fixture-banner]'))
check('default count is 10', (await page.textContent('[data-testid=count-value]')) === '10')
await page.screenshot({ path: `${OUT}/02-connected.png`, fullPage: true })

// 3. choose 8 with the stepper, import, watch the progress
await page.click('[aria-label="fewer reels"]'); await page.click('[aria-label="fewer reels"]')
check('stepper to 8', (await page.textContent('[data-testid=count-value]')) === '8')
await page.click('[data-testid=import]')
await page.waitForSelector('[data-testid=progress]', { timeout: 15000 })
check('progress bar appears', true)
await page.waitForTimeout(3000)
await page.screenshot({ path: `${OUT}/03-progress.png`, fullPage: true })
await page.waitForSelector('[data-testid=import-result]', { timeout: 180000 })
const resText = await page.textContent('[data-testid=import-result]')
check('import result says 8 of 8', /Imported 8 of 8/.test(resText), resText.trim())
await page.waitForSelector('[data-testid=dna]')
check('DNA card + sample size', /8 reels/.test(await page.textContent('[data-testid=dna]')))
check('DNA states not proof', /not proof/.test(await page.textContent('[data-testid=dna]')))
const rows = await page.$$('[data-testid=reel-row]')
check('8 reel rows', rows.length === 8, String(rows.length))
const reelsText = await page.textContent('[data-testid=reels]')
check('reels show index and watch-through', /index \d\.\d\d/.test(reelsText) && /watch-through \d+%/.test(reelsText))
check('missing numbers say not available (no invented values)', /not available/.test(reelsText))
check('every reel marked FIXTURE', (reelsText.match(/FIXTURE/g) || []).length >= 8)
await page.screenshot({ path: `${OUT}/04-imported.png`, fullPage: true })

// 4. mobile layout
await page.setViewportSize({ width: 390, height: 844 })
await page.reload(); await page.waitForSelector('[data-testid=dna]')
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
check('mobile: no horizontal scroll', overflow <= 1, `overflow=${overflow}`)
await page.screenshot({ path: `${OUT}/05-mobile.png`, fullPage: true })
await page.setViewportSize({ width: 1100, height: 900 })

// 5. results page with the personalised analysis
const result = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
await page.evaluate(r => { localStorage.setItem('prysm_result', JSON.stringify(r)); localStorage.setItem('prysm_video_source', 'upload') }, result)
await page.goto(`${BASE}/results`)
await page.waitForSelector('[data-testid=viewer-behaviour]', { timeout: 30000 })
await page.waitForSelector('[data-testid=measured]', { timeout: 15000 })
const vb = await page.textContent('[data-testid=viewer-behaviour]')
check('viewer behaviour: measured from your account', /Measured from your account/.test(vb) && /Average watch-through/.test(vb) && /Average skip rate/.test(vb) && /Typical reach/.test(vb))
check('viewer behaviour: labelled test data', /TEST DATA/.test(vb))
check('viewer behaviour: no Qwen bars', !(await page.isVisible('[data-testid=estimate]')) && !/Hook Hold Rate/.test(vb))
check('personalised tag shown', await page.isVisible('[data-testid=personalised-tag]'))
check('comparison card shown', await page.isVisible('[data-testid=creator-dna-card]'))
const card = await page.textContent('[data-testid=creator-dna-card]')
check('comparison card: title with N', new RegExp('How this compares to your last ' + result.creatorDna.sampleSize + ' reels').test(card))
check('comparison card: cites reels', /Reels? \d/.test(card))
await page.screenshot({ path: `${OUT}/06-results-personalised.png`, fullPage: false })
const el = await page.$('[data-testid=viewer-behaviour]'); await el.scrollIntoViewIfNeeded()
await page.screenshot({ path: `${OUT}/07-results-viewer-behaviour.png` })
const elc = await page.$('[data-testid=creator-dna-card]'); await elc.scrollIntoViewIfNeeded()
await page.screenshot({ path: `${OUT}/08-results-compare.png` })

// 6. disconnect from the page
await page.goto(`${BASE}/creator-dna`)
await page.waitForSelector('[data-testid=disconnect]')
await page.click('[data-testid=disconnect]')
await page.click('[data-testid=disconnect-confirm]')
await page.waitForSelector('[data-testid=consent]')
check('after disconnect: consent screen again', true)
const dna = await (await api('/instagram/dna')).json()
check('after disconnect: API has no DNA or reels', dna.connected === false)
await page.screenshot({ path: `${OUT}/09-disconnected.png`, fullPage: true })

// 7. results page when not connected: no bars, a connect line
const scrubbed = { ...result }; delete scrubbed.creatorDna
await page.evaluate(r => localStorage.setItem('prysm_result', JSON.stringify(r)), scrubbed)
await page.goto(`${BASE}/results`)
await page.waitForSelector('[data-testid=connect-line]', { timeout: 30000 })
const vb2 = await page.textContent('[data-testid=viewer-behaviour]')
check('not connected: connect line', /Connect Instagram to see viewer behaviour measured from your own reels/.test(vb2))
check('not connected: no percentage bars', !/%/.test(vb2.replace(/Qwen|Q-Align/g, '')) || !(await page.isVisible('[data-testid=estimate]')))
check('not connected: no comparison card', !(await page.isVisible('[data-testid=creator-dna-card]')))
const q = await page.isVisible('text=Visual quality signal')
console.log('INFO Q-Align box present:', q)
const a = await page.$('[data-testid=connect-line] a'); check('connect line links to /creator-dna', (await a.getAttribute('href')) === '/creator-dna')
const vbEl = await page.$('[data-testid=viewer-behaviour]'); await vbEl.scrollIntoViewIfNeeded()
await page.screenshot({ path: `${OUT}/10-results-not-connected.png` })

check('no uncaught page errors', consoleErrors.length === 0, consoleErrors.join(' | ').slice(0, 300))
await browser.close()
const failed = results.filter(r => !r[0])
console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
process.exit(failed.length ? 1 : 0)
