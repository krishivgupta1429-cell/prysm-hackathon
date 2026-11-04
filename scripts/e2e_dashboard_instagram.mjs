// Built during the Meta Global AI Developer Hackathon (new work).
// Headless-Chrome check of the Instagram card on the Dashboard (1280 and 390 wide) in fixture mode: not connected, the Connect button, the way back from
// /creator-dna, connected (no manual refresh), Disconnect, a failing status call, Instagram not configured, and Settings without the old card.
//   node scripts/e2e_dashboard_instagram.mjs --web http://localhost:5195 --api http://127.0.0.1:8782 --out samples/_local/dashboard-ig-shots
// Needs a backend with INSTAGRAM_FIXTURE=1 and the local test user from the dashboard seed (dashboardtester@gmail.com).
import { createRequire } from 'module'
import fs from 'fs'
const require = createRequire(new URL('../web/', import.meta.url).pathname)
const { chromium } = require('playwright-core')
const arg = (n, d) => { const i = process.argv.indexOf('--' + n); return i > 0 ? process.argv[i + 1] : d }
const WEB = arg('web', 'http://localhost:5195'), API = arg('api', 'http://127.0.0.1:8782'), OUT = arg('out', 'samples/_local/dashboard-ig-shots')
fs.mkdirSync(OUT, { recursive: true })
let fails = 0
const check = (name, ok, extra = '') => { if (!ok) fails++; console.log(ok ? 'PASS' : 'FAIL', name, extra) }
const login = await (await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: 'dashboardtester@gmail.com', password: 'DashTest-12345' }), signal: AbortSignal.timeout(20000) })).json()
if (!login.access_token) { console.log('FAIL login'); process.exit(1) }
await fetch(`${API}/instagram/disconnect`, { method: 'DELETE', headers: { authorization: `Bearer ${login.access_token}` } })
const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true })
const VIEWS = { desktop: { viewport: { width: 1280, height: 900 } }, mobile: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 } }
for (const [v, vopts] of Object.entries(VIEWS)) {
  const ctx = await browser.newContext(vopts)
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(String(e)))
  await page.goto(`${WEB}/login`)
  await page.evaluate(([a, b]) => { localStorage.setItem('prysm_token', a); localStorage.setItem('prysm_refresh', b) }, [login.access_token, login.refresh_token])
  const overflow = async (l) => { const o = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth); check(`${v} ${l}: no horizontal scroll`, o <= 1, `(${o}px)`) }
  const shotTop = async (n) => { await page.waitForTimeout(900); await page.screenshot({ path: `${OUT}/${v}-${n}.png`, clip: { x: 0, y: 0, width: vopts.viewport.width, height: v === 'mobile' ? 1000 : 700 } }) }

  // 1. not connected
  await page.goto(`${WEB}/dashboard`); await page.waitForSelector('[data-testid=ig-card-connect]')
  check(`${v}: not connected card`, /Connect Instagram/.test(await page.textContent('[data-testid=ig-card-connect]')) && /Personalise every analysis with your real reels/.test(await page.textContent('[data-testid=ig-card-connect]')))
  check(`${v}: the card sits directly under the three action cards`, await page.evaluate(() => {
    const card = document.querySelector('[data-testid=ig-card-connect]').getBoundingClientRect(); const chat = [...document.querySelectorAll('a')].find(a => /Chat with Prysm/.test(a.textContent)).getBoundingClientRect()
    return card.top >= chat.bottom && card.top - chat.bottom < 60 }))
  await overflow('not connected'); await shotTop('1-not-connected')
  // 2. Connect goes to /creator-dna, which has a Back link; connecting (fixture) and coming back shows the connected card without a refresh
  await page.click('[data-testid=ig-connect]'); await page.waitForURL('**/creator-dna'); await page.waitForSelector('[data-testid=connect]')
  check(`${v}: /creator-dna has a Back to dashboard link`, (await page.getAttribute('[data-testid=back-to-dashboard]', 'href')) === '/dashboard')
  await page.click('[data-testid=connect]'); await page.waitForSelector('[data-testid=import-card]')
  await page.click('[data-testid=back-to-dashboard]'); await page.waitForURL('**/dashboard'); await page.waitForSelector('[data-testid=ig-card-connected]')
  const t = await page.textContent('[data-testid=ig-card-connected]')
  check(`${v}: connected card updated by itself`, /Connected as @fixture_creator/.test(t) && /No reels imported yet|reels? imported/.test(t) && /View Creator DNA/.test(t) && /Disconnect/.test(t), `(${t.replace(/\s+/g, ' ').slice(0, 90)})`)
  await overflow('connected'); await shotTop('2-connected')
  // 3. Disconnect asks first, then returns to the connect card
  await page.click('[data-testid=ig-disconnect]'); await page.waitForSelector('[data-testid=ig-disconnect-dialog]'); await page.screenshot({ path: `${OUT}/${v}-3-disconnect-dialog.png` })
  await page.click('[data-testid=ig-disconnect-confirm]'); await page.waitForSelector('[data-testid=ig-card-connect]')
  check(`${v}: disconnect returns to the connect card`, true)
  // 4. a failing status call shows the connect card, never an error; not configured hides the card
  await page.route('**/instagram/status', r => r.abort())
  await page.reload(); await page.waitForSelector('[data-testid=ig-card-connect]')
  check(`${v}: failing status shows the connect card, no error text`, !/error|failed|could not/i.test(await page.textContent('[data-testid=ig-card-connect]')))
  await page.unroute('**/instagram/status')
  await page.route('**/instagram/status', async r => { const j = await (await r.fetch()).json(); await r.fulfill({ json: { ...j, configured: false, fixtureAvailable: false } }) })
  await page.reload(); await page.waitForSelector('text=Upload video'); await page.waitForTimeout(1500)
  check(`${v}: not configured hides the card`, (await page.locator('[data-testid^=ig-card]').count()) === 0)
  await shotTop('4-hidden'); await page.unroute('**/instagram/status')
  // 5. Settings no longer has the card
  await page.goto(`${WEB}/settings`); await page.waitForSelector('text=Manage your account')
  const st = await page.textContent('body')
  check(`${v}: Settings has no Instagram card or Integrations heading, keeps Email and Creator Handle`, !/Instagram|Integrations|Creator DNA/i.test(st) && /Email/.test(st) && /Handle/i.test(st))
  await overflow('settings'); await page.screenshot({ path: `${OUT}/${v}-5-settings.png`, fullPage: true })
  check(`${v}: no page errors`, errors.length === 0, errors.join(' | ').slice(0, 200))
  await ctx.close()
}
await browser.close()
console.log(fails ? `${fails} FAILED` : 'ALL PASS'); process.exit(fails ? 1 : 0)
