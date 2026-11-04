// Built during the Meta Global AI Developer Hackathon (new work).
// Headless-Chrome check of the Creator DNA page before Instagram is connected (1280 and 390 wide), with the not-configured and failed-connection states.
//   node scripts/e2e_creator_connect.mjs --web http://localhost:5195 --api http://127.0.0.1:8782 --out samples/_local/creator-connect-shots
// Uses a local test user that has not connected Instagram (dashboardtester@gmail.com).
import { createRequire } from 'module'
import fs from 'fs'
const require = createRequire(new URL('../web/', import.meta.url).pathname)
const { chromium } = require('playwright-core')
const arg = (n, d) => { const i = process.argv.indexOf('--' + n); return i > 0 ? process.argv[i + 1] : d }
const WEB = arg('web', 'http://localhost:5195'), API = arg('api', 'http://127.0.0.1:8782'), OUT = arg('out', 'samples/_local/creator-connect-shots')
fs.mkdirSync(OUT, { recursive: true })
let fails = 0
const check = (name, ok, extra = '') => { if (!ok) fails++; console.log(ok ? 'PASS' : 'FAIL', name, extra) }
const login = await (await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: 'dashboardtester@gmail.com', password: 'DashTest-12345' }), signal: AbortSignal.timeout(20000) })).json()
if (!login.access_token) { console.log('FAIL login'); process.exit(1) }
await fetch(`${API}/instagram/disconnect`, { method: 'DELETE', headers: { authorization: `Bearer ${login.access_token}` } })
const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true })
const VIEWS = { desktop: { viewport: { width: 1280, height: 900 } }, mobile: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 } }
for (const [vname, vopts] of Object.entries(VIEWS)) {
  const ctx = await browser.newContext(vopts)
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(String(e)))
  await page.goto(`${WEB}/login`)
  await page.evaluate(([a, b]) => { localStorage.setItem('prysm_token', a); localStorage.setItem('prysm_refresh', b) }, [login.access_token, login.refresh_token])
  const noOverflow = async (l) => { const o = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth); check(`${vname} ${l}: no horizontal scroll`, o <= 1, `(${o}px)`) }
  // 1. the normal page
  await page.goto(`${WEB}/creator-dna`); await page.waitForSelector('[data-testid=consent]')
  const t = await page.textContent('[data-testid=consent]')
  check(`${vname}: headline + value sentence`, /Learn what works in your own reels/.test(t) && /See what actually works in your own reels, based on the numbers Instagram measured\./.test(t))
  check(`${vname}: three benefits`, (await page.locator('[data-testid=benefits] li').count()) === 3)
  check(`${vname}: one button "Connect Instagram", no checkbox`, (await page.locator('[data-testid=consent] button').count()) === 1 && /Connect Instagram/.test(await page.textContent('[data-testid=connect]')) && (await page.locator('input[type=checkbox], [role=checkbox]').count()) === 0)
  check(`${vname}: muted line`, /Read-only access\. Disconnect anytime\./.test(t))
  check(`${vname}: old text gone`, !/What we read|What we store|Who processes|Deleting it|Permissions asked|downloaded for a moment|Business or Creator Instagram account|I understand/i.test(t))
  await noOverflow('connect page'); await page.screenshot({ path: `${OUT}/${vname}-1-connect.png`, fullPage: true })
  // 2. the connection comes back failed: the Creator or Business hint appears now, not up front
  await page.goto(`${WEB}/creator-dna?instagram=error`); await page.waitForSelector('[data-testid=page-error]')
  check(`${vname}: failed connection shows the Creator/Business hint`, /Creator or Business account in Instagram settings, then try again/.test(await page.textContent('[data-testid=page-error]')))
  await page.screenshot({ path: `${OUT}/${vname}-2-failed.png`, fullPage: true })
  // 3. the server says Instagram is not configured (status says so; connect answers 503)
  await page.route('**/instagram/status', async r => { const j = await (await r.fetch()).json(); await r.fulfill({ json: { ...j, configured: false, fixtureAvailable: false } }) })
  await page.goto(`${WEB}/creator-dna`); await page.waitForSelector('[data-testid=not-available]')
  check(`${vname}: not configured gives a short neutral message and a disabled button`, /not available right now/.test(await page.textContent('[data-testid=not-available]')) && await page.isDisabled('[data-testid=connect]'))
  await page.screenshot({ path: `${OUT}/${vname}-3-not-configured.png`, fullPage: true })
  await page.unroute('**/instagram/status')
  await page.route('**/instagram/connect', r => r.fulfill({ status: 503, json: { detail: 'Instagram is not configured on this server (app id, app secret and redirect URI).' } }))
  await page.goto(`${WEB}/creator-dna`); await page.waitForSelector('[data-testid=connect]'); await page.click('[data-testid=connect]')
  await page.waitForSelector('[data-testid=page-error]')
  const e = await page.textContent('[data-testid=page-error]')
  check(`${vname}: a 503 from connect shows the neutral message, not the server detail`, /not available right now/.test(e) && !/app secret/.test(e))
  check(`${vname}: no page errors`, errors.length === 0, errors.join(' | ').slice(0, 200))
  await ctx.close()
}
await browser.close()
console.log(fails ? `${fails} FAILED` : 'ALL PASS'); process.exit(fails ? 1 : 0)
