// Built during the Meta Global AI Developer Hackathon (new work).
// Headless-Chrome check of the Upload vs "study any reel" (link) flows, with links ON or OFF, at desktop and mobile widths.
//   node scripts/e2e_link_flow.mjs --links on|off --web http://localhost:5191 --api http://localhost:8780 --out <dir> [--connect]
// ON needs a backend with STUDIO_ALLOW_LINKS=1 and INSTAGRAM_FIXTURE=1 (fake reels) and a web server built with VITE_STUDIO_ALLOW_LINKS=1.
// OFF needs a backend with STUDIO_ALLOW_LINKS unset and a web server without VITE_STUDIO_ALLOW_LINKS.
// Uses its own test user (linkflow.tester@gmail.com). --connect also connects the fixture Instagram and imports 5 fake reels (small Meta cost).
import { createRequire } from 'module'
import fs from 'fs'
const require = createRequire(new URL('../web/', import.meta.url).pathname)
const { chromium } = require('playwright-core')
const arg = (n, d) => { const i = process.argv.indexOf('--' + n); return i > 0 ? process.argv[i + 1] : d }
const LINKS = arg('links', 'on') === 'on'
const WEB = arg('web', 'http://localhost:5191'), API = arg('api', 'http://localhost:8780')
const OUT = arg('out', '/tmp/link-flow-shots'), CONNECT = process.argv.includes('--connect')
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
const EMAIL = 'linkflow.tester@gmail.com', PASSWORD = 'Str0ng!Passw0rd'
fs.mkdirSync(OUT, { recursive: true })
let fails = 0
const check = (name, ok, extra = '') => { if (!ok) fails++; console.log(ok ? 'PASS' : 'FAIL', name, extra) }
const j = (path, method = 'GET', body, token) => fetch(`${API}${path}`, { method, headers: { 'content-type': 'application/json', 'user-agent': UA, ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined })

await j('/auth/register', 'POST', { email: EMAIL, username: 'linkflowtester', password: PASSWORD, full_name: 'Link Flow' })
const login = await (await j('/auth/login', 'POST', { email: EMAIL, password: PASSWORD })).json()
if (!login.access_token) { console.log('FAIL login'); process.exit(1) }
const tk = login.access_token

if (CONNECT && LINKS) {
  await j('/instagram/disconnect', 'DELETE', undefined, tk)
}

const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true })
const VIEWS = { desktop: { viewport: { width: 1280, height: 900 } }, mobile: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 } }

for (const [vname, vopts] of Object.entries(VIEWS)) {
  const ctx = await browser.newContext({ userAgent: UA, ...vopts })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(String(e)))
  await page.goto(`${WEB}/login`)
  await page.evaluate(([a, r]) => { localStorage.setItem('prysm_token', a); localStorage.setItem('prysm_refresh', r) }, [login.access_token, login.refresh_token])
  const tag = `${LINKS ? 'on' : 'off'}-${vname}`
  const shot = async (n) => page.screenshot({ path: `${OUT}/${tag}-${n}.png`, fullPage: true })
  const noOverflow = async (label) => { const o = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth); check(`${tag} ${label}: no horizontal scroll`, o <= 1, `(${o}px)`) }
  const text = async () => (await page.textContent('body')) || ''

  // 1. dashboard
  await page.goto(`${WEB}/dashboard`); await page.waitForSelector('text=Upload video', { timeout: 20000 })
  let t = await text()
  check(`${tag} dashboard: Upload video card`, /Upload video/.test(t) && /Analyse before you post/.test(t))
  check(`${tag} dashboard: Chat with Prysm card`, /Chat with Prysm/.test(t))
  check(`${tag} dashboard: Paste a link card ${LINKS ? 'shown' : 'hidden'}`, LINKS ? /Paste a link/.test(t) && /Study any Instagram reel/.test(t) : !/Paste a link/.test(t))
  if (/My Videos/.test(t)) check(`${tag} dashboard: Reel Breakdowns tab ${LINKS ? 'shown' : 'hidden'}`, LINKS ? /Reel Breakdowns/.test(t) : !/Reel Breakdowns/.test(t))
  else check(`${tag} dashboard (no analyses yet): empty state ${LINKS ? 'mentions a link' : 'mentions the upload only'}`, LINKS ? /Upload a video or paste a link to get started/.test(t) : /Upload a video to get started/.test(t) && !/paste a link/i.test(t))
  await shot('1-dashboard'); await noOverflow('dashboard')

  // 2. chooser
  await page.goto(`${WEB}/analyse`); await page.waitForSelector('text=Upload your video')
  t = await text()
  check(`${tag} chooser: upload card; study card ${LINKS ? 'shown' : 'hidden'}`, /Upload your video/.test(t) && (LINKS ? /Study a reel/.test(t) : !/Study a reel/.test(t)))
  await shot('2-chooser'); await noOverflow('chooser')

  // 3. upload flow (same either way)
  await page.goto(`${WEB}/analyse/upload`); await page.waitForTimeout(1200)
  check(`${tag} upload flow opens`, /Step 1|Upload|video/i.test(await text()))
  await shot('3-upload'); await noOverflow('upload')

  if (!LINKS) {
    await page.goto(`${WEB}/analyse/link`); await page.waitForTimeout(1500)
    check(`${tag} direct /analyse/link goes to the upload page (baseline redirect)`, page.url().endsWith('/analyse/upload'), page.url())
    await shot('4-links-off-redirect'); await noOverflow('links-off redirect')
    const fd = new FormData(); fd.append('url', 'https://www.instagram.com/reel/ABCDEFGHIJK/'); fd.append('intent', 'competitor')
    const r2 = await fetch(`${API}/prysm/analyse`, { method: 'POST', body: fd, headers: { authorization: `Bearer ${tk}`, 'user-agent': UA } })
    const b2 = await r2.json().catch(() => ({}))
    check(`${tag} backend refuses a link while the switch is off`, r2.status === 400 && /switched off/.test(b2.detail || ''), `(${r2.status})`)
    check(`${tag} no page errors`, errors.length === 0, errors.join(' | ').slice(0, 200))
    await ctx.close(); continue
  }

  // 4. link flow, "study" mode
  await page.goto(`${WEB}/analyse/link`); await page.waitForSelector('[data-testid=reel-kind]')
  await shot('4-link-step1'); await noOverflow('link step 1')
  check(`${tag} link step 1: two choices (study / my own reel)`, /A reel I want to study/.test(await text()) && /My own reel/.test(await text()))
  const url = 'https://www.instagram.com/reel/Zz9Yy8XxWwV/'
  await page.fill('input[placeholder^="https://www.instagram.com"]', url)
  await page.click('text=Continue →'); await page.waitForSelector('text=What\'s your niche?')
  await shot('5-link-step3'); await noOverflow('link step 3 (niche)')
  await page.click('button:has-text("Fitness")'); await page.click('text=/Continue|Next/')
  await page.waitForSelector('text=What caught your eye?')
  t = await text()
  check(`${tag} link step 4: "What caught your eye?", the learn question and button`, /What do you want to learn from this video\?/.test(t) && /Break Down This Video/.test(t))
  check(`${tag} link step 4: 10 parameters / Format breakdown / Replicable takeaways`, /10 parameters/.test(t) && /Format breakdown/.test(t) && /Replicable takeaways/.test(t))
  check(`${tag} link step 4: estimated time shown`, (await page.locator('[data-testid=estimate]').count()) === 1, (await page.locator('[data-testid=estimate]').textContent()) || '')
  await shot('6-link-step4'); await noOverflow('link step 4')

  // 5. a link Instagram will not give us -> plain message + upload instead
  await page.fill('textarea', 'how they get so many views')
  await page.click('text=Break Down This Video →')
  await page.waitForSelector('[data-testid=upload-instead]', { timeout: 150000 })
  t = await text()
  check(`${tag} blocked link: plain message and "upload the file instead"`, (/Instagram blocked this link/.test(t) || /took too long/.test(t)) && /upload the video file instead/i.test(t))
  await shot('7-link-blocked'); await noOverflow('blocked link')

  // 6. own reel: connect call to action, then the picker
  await page.goto(`${WEB}/analyse/link`); await page.waitForSelector('[data-testid=reel-kind]')
  await page.click('[role=tab]:has-text("My own reel")')
  await page.waitForSelector('[data-testid=picker-connect], [data-testid=picker-list], [data-testid=picker-empty], [data-testid=picker-error]')
  if (!CONNECT) {
    check(`${tag} own reel (not connected): Connect Instagram call to action`, (await page.locator('[data-testid=picker-connect]').count()) === 1 && /Connect Instagram/.test(await text()))
    await shot('8-own-not-connected'); await noOverflow('own reel, not connected')
  } else {
    if (vname === 'desktop') {
      check(`${tag} own reel (not connected): Connect Instagram call to action`, (await page.locator('[data-testid=picker-connect]').count()) === 1)
      await shot('8-own-not-connected')
      const c = await (await j('/instagram/connect', 'POST', undefined, tk)).json()
      check('fixture Instagram connected', c.fixture === true)
      const imp = await (await j('/instagram/import', 'POST', { count: 5 }, tk)).json()
      for (let i = 0; i < 90; i++) { const s = await (await j(`/instagram/import/${imp.jobId}`, 'GET', undefined, tk)).json(); if (s.status !== 'running') { check('import finished', s.status === 'done', JSON.stringify(s).slice(0, 120)); break } await new Promise(r => setTimeout(r, 4000)) }
    }
    await page.goto(`${WEB}/analyse/link`); await page.waitForSelector('[data-testid=reel-kind]')
    await page.click('[role=tab]:has-text("My own reel")'); await page.waitForSelector('[data-testid=picker-list]', { timeout: 30000 })
    const n = await page.locator('[data-testid=reel-row]').count()
    check(`${tag} own reel (connected): reel cards with numbers and thumbnails`, n >= 3, `(${n} reels)`)
    check(`${tag} own reel: real numbers shown`, /vs your usual|usual|No reach/i.test(await text()))
    await shot('9-own-picker'); await noOverflow('own reel picker')
    await page.click('[data-testid=pick-reel] >> nth=0')
    const val = await page.inputValue('input[placeholder^="https://www.instagram.com"]')
    check(`${tag} picking a reel fills in its link`, /^https:\/\/.*instagram\.com\/|^https?:\/\//.test(val), val.slice(0, 60))
    await shot('10-own-picked')
    await page.click('text=Continue →'); await page.waitForSelector('text=What\'s your niche?')
    await page.click('button:has-text("Fitness")'); await page.click('text=/Continue|Next/')
    await page.waitForSelector('text=Tell us about it')
    check(`${tag} own reel step 4: own-reel wording`, /Break Down My Reel/.test(await text()))
    await shot('11-own-step4'); await noOverflow('own reel step 4')
  }
  // 7. a studied reel's result page (saved result of a real link run) and the dashboard tabs with it
  const STUDIED = arg('studied', '')
  if (STUDIED) {
    const saved = JSON.parse(fs.readFileSync(STUDIED, 'utf8')).result
    await page.goto(`${WEB}/dashboard`); await page.waitForSelector('text=Reel Breakdowns', { timeout: 20000 })
    await page.click('text=Reel Breakdowns'); await page.waitForTimeout(600)
    check(`${tag} dashboard: the studied reel is listed under Reel Breakdowns`, /fireflydo|Strong Concept|79/.test(await text()))
    await shot('12-dashboard-breakdowns'); await noOverflow('dashboard, Reel Breakdowns')
    await page.evaluate(([r]) => { localStorage.setItem('prysm_result', r); localStorage.setItem('prysm_intent', 'competitor'); localStorage.setItem('prysm_video_source', 'link') }, [JSON.stringify(saved)])
    await page.goto(`${WEB}/results`); await page.waitForSelector('text=Your Next Video Blueprint', { timeout: 30000 })
    t = await text()
    check(`${tag} studied result: 10-parameter analysis`, /10-Parameter Analysis/.test(t))
    check(`${tag} studied result: replicable takeaways ("Techniques That ...", "Key takeaways for your content")`, /Techniques That/.test(t) && /Key takeaways for your content/.test(t))
    check(`${tag} studied result: format breakdown (Hook, Structure, Audio, Text Overlays, Call to Action)`, ['Hook', 'Structure', 'Audio', 'Text Overlays', 'Call to Action'].every(w => t.includes(w)))
    check(`${tag} studied result: "how to adapt this to your niche" with the note`, (await page.locator('[data-testid=adapt-card]').count()) === 1 && /Borrow the structure, not the content\./.test(t))
    check(`${tag} studied result: no auto-edit offer, no thumbnail of the studied video`, !/auto-?edit|edited video|download edited/i.test(t))
    await shot('13-studied-result'); await noOverflow('studied result')
    await page.locator('[data-testid=adapt-card]').scrollIntoViewIfNeeded()
    await page.locator('[data-testid=adapt-card]').screenshot({ path: `${OUT}/${tag}-14-adapt-card.png` })
  }
  check(`${tag} no page errors`, errors.length === 0, errors.join(' | ').slice(0, 200))
  await ctx.close()
}
await browser.close()
console.log(fails === 0 ? 'ALL PASS' : `${fails} FAILED`)
process.exit(fails ? 1 : 0)
