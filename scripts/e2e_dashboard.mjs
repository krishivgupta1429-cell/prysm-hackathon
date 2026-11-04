// Built during the Meta Global AI Developer Hackathon (new work).
// Headless-Chrome check of /dashboard at desktop (1280) and mobile (390) for a seeded LOCAL test account (10 own videos, 3 studied reels).
//   node scripts/e2e_dashboard.mjs --web http://localhost:5195 --api http://127.0.0.1:8775 --out samples/_local/dashboard-shots [--email ... --password ...]
// The account is created by a local fixture seed (never the deployed database).
import { createRequire } from 'module'
import fs from 'fs'
const require = createRequire(new URL('../web/', import.meta.url).pathname)
const { chromium } = require('playwright-core')
const arg = (n, d) => { const i = process.argv.indexOf('--' + n); return i > 0 ? process.argv[i + 1] : d }
const WEB = arg('web', 'http://localhost:5195'), API = arg('api', 'http://127.0.0.1:8775'), OUT = arg('out', 'samples/_local/dashboard-shots')
const EMAIL = arg('email', 'dashboardtester@gmail.com'), PASSWORD = arg('password', 'DashTest-12345'), TAG = arg('tag', 'seeded')
fs.mkdirSync(OUT, { recursive: true })
let fails = 0
const check = (name, ok, extra = '') => { if (!ok) fails++; console.log(ok ? 'PASS' : 'FAIL', name, extra) }
const r = await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: EMAIL, password: PASSWORD }), signal: AbortSignal.timeout(20000) })
const login = await r.json()
if (!login.access_token) { console.log('FAIL login'); process.exit(1) }
const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true })
const VIEWS = { desktop: { viewport: { width: 1280, height: 900 } }, mobile: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 } }
for (const [vname, vopts] of Object.entries(VIEWS)) {
  const ctx = await browser.newContext(vopts)
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(String(e)))
  await page.goto(`${WEB}/login`)
  await page.evaluate(([a, b]) => { localStorage.setItem('prysm_token', a); localStorage.setItem('prysm_refresh', b) }, [login.access_token, login.refresh_token])
  await page.goto(`${WEB}/dashboard`)
  await page.waitForSelector('text=Upload video', { timeout: 20000 })
  await page.waitForTimeout(2500) // the reveal animations
  const t = (await page.textContent('body')) || ''
  const tag = `${TAG}-${vname}`
  const has = (label, s) => check(`${tag}: ${label}`, t.toLowerCase().includes(s.toLowerCase()), `(${s})`)
  has('greeting', 'dashtester')
  has('videos chip', '10 videos analysed'); has('competitors chip', '3 competitors studied')
  has('avg score ring label', 'Your avg score')
  has('card Upload video', 'Upload video'); has('card Paste a link', 'Paste a link'); has('card Chat with Prysm', 'Chat with Prysm')
  has('coach card', 'Prysm coach'); has('coach strongest signal', 'Your strongest signal is'); has('coach recurring theme', 'keeps coming up')
  has('best vs latest', 'Your best vs latest')
  has('tab My Videos', 'My Videos'); has('tab Reel Breakdowns', 'Reel Breakdowns')
  has('score over time', 'Score over time'); has('strengths and weaknesses', 'Strengths & Weaknesses'); has('keep working on', 'Keep working on')
  check(`${tag}: no credits meter / pricing / upgrade`, !/credits|upgrade|pricing/i.test(t) && (await page.locator('a[href="/pricing"]').count()) === 0)
  check(`${tag}: row feedback buttons`, (await page.locator('button:has-text("Report Issue"), button:has-text("Drop Feedback")').count()) >= 10)
  const o = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  check(`${tag}: no horizontal scroll`, o <= 1, `(${o}px)`)
  await page.screenshot({ path: `${OUT}/${tag}-1-top.png` })
  await page.screenshot({ path: `${OUT}/${tag}-2-full.png`, fullPage: true })
  await page.click('text=Reel Breakdowns'); await page.waitForTimeout(500)
  await page.screenshot({ path: `${OUT}/${tag}-3-reel-breakdowns.png`, fullPage: true })
  check(`${tag}: no page errors`, errors.length === 0, errors.join(' | ').slice(0, 200))
  await ctx.close()
}
await browser.close()
console.log(fails ? `${fails} FAILED` : 'ALL PASS'); process.exit(fails ? 1 : 0)
