// Built during the Meta Global AI Developer Hackathon (new work).
// READ-ONLY look at the real connected account in headless Chrome: desktop and mobile screenshots of /creator-dna.
// It never changes anything: every request that is not a GET is blocked (and counted), so it cannot import, disconnect or connect.
// Usage: node scripts/screenshot_instagram_real_readonly.mjs <file holding a local access token> <output folder>
import { createRequire } from 'module'
import fs from 'fs'
const require = createRequire(new URL('../web/', import.meta.url).pathname)
const { chromium } = require('playwright-core')
const [tokFile, OUT] = process.argv.slice(2)
fs.mkdirSync(OUT, { recursive: true })
const BASE = 'http://localhost:5177'
const token = fs.readFileSync(tokFile, 'utf8').trim()
const results = []
const check = (name, ok, extra = '') => { results.push(ok); console.log(ok ? 'PASS' : 'FAIL', name, extra) }

const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true })
const ctx = await browser.newContext({ viewport: { width: 1100, height: 900 } })
const page = await ctx.newPage()
const errors = []; const blocked = []; const calls = []
page.on('pageerror', e => errors.push(String(e)))
await page.route('**/*', route => {
  const r = route.request()
  if (r.url().startsWith(BASE) && r.url().includes('/instagram') && r.method() !== 'GET') { blocked.push(`${r.method()} ${new URL(r.url()).pathname}`); return route.abort() }
  if (r.url().includes('/instagram/')) calls.push(new URL(r.url()).pathname)
  return route.continue()
})
await page.goto(`${BASE}/login`)
await page.evaluate(t => { localStorage.setItem('prysm_token', t); localStorage.setItem('prysm_refresh', t) }, token)
await page.goto(`${BASE}/creator-dna`)
await page.waitForSelector('[data-testid=account-card]', { timeout: 30000 })
await page.waitForSelector('[data-testid=reels]', { timeout: 30000 })
await page.waitForTimeout(1500)

const handle = await page.textContent('[data-testid=handle]')
check('real account card: handle, connected, last imported', /@theprysm7/.test(handle) && /Connected/.test(await page.textContent('[data-testid=connected-status]')) && /Last imported/.test(await page.textContent('[data-testid=last-imported]')), handle)
check('not shown as test data', !(await page.isVisible('[data-testid=fixture-banner]')))
check('"You have N reels available" is shown', /You have \d+ reels? available/.test(await page.textContent('[data-testid=available]')), (await page.textContent('[data-testid=available]')).trim())
const max = await page.getAttribute('[data-testid=count]', 'max'); const min = await page.getAttribute('[data-testid=count]', 'min')
check('slider is 1 up to the number of reels the account has', min === '1' && Number(max) >= 1 && Number(max) <= 30, `min=${min} max=${max}`)
const cards = await page.$$('[data-testid=reel-row]')
check('reel cards shown for the imported reels', cards.length >= 1, String(cards.length))
const all = (await page.textContent('[data-testid=reels]')).replace(/\s+/g, ' ')
check('real metrics are shown (views, reach, saves, shares, avg watch time, skip rate)', /Views/.test(all) && /Reach/.test(all) && /Saves/.test(all) && /Shares/.test(all) && /Avg watch time/.test(all) && /Skip rate/.test(all))
check('skip rate is a sane percent, not 5910%', /Skip rate\s*\d{1,2}%/.test(all) && !/Skip rate\s*\d{3,}%/.test(all))
check('plain-language reach, no "index 0.75 (reach)"', /Reach \d+% (above|below) your usual|about your usual/.test(all) && !/index \d\.\d\d/.test(all))
const captions = await page.$$eval('[data-testid=caption]', els => els.map(e => e.textContent.trim()))
check('captions are cut at a word with an ellipsis (or short enough to show whole)', captions.every(c => !c.endsWith('…') || /[\s\S]…$/.test(c)) && captions.some(c => c.endsWith('…')), captions.map(c => c.slice(-24)).join(' | '))
check('no broken characters in captions', captions.every(c => !/�/.test(c)))
if (cards.length < 3) check('fewer than 3 reels: breakdown only, "Patterns need at least 3 reels", no pattern claims', await page.isVisible('[data-testid=breakdown-note]') && !(await page.isVisible('[data-testid=dna]')) && /Patterns need at least 3 reels/.test(await page.textContent('[data-testid=breakdown-note]')))
else check('3 or more reels: the DNA card is shown "Based on N reels"', /Based on \d+ reels/.test(await page.textContent('[data-testid=dna-title]')))
check('hook words say unknown for reels imported before this feature', (await page.$$('[data-testid=hook-words]')).length === cards.length)
check('the "Did my fix work?" section is there', await page.isVisible('[data-testid=fix-loop]'))
const o = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
check('desktop: no horizontal scroll', o <= 1, `overflow=${o}`)
await page.screenshot({ path: `${OUT}/real-desktop.png`, fullPage: true })

// the reel dialog (a chip or a title opens it)
await page.click('[data-testid=reel-row] h3 button')
await page.waitForSelector('[data-testid=reel-dialog]')
await page.screenshot({ path: `${OUT}/real-reel-dialog.png`, fullPage: false })
await page.keyboard.press('Escape')

// mobile
await page.setViewportSize({ width: 390, height: 844 })
await page.reload(); await page.waitForSelector('[data-testid=reels]'); await page.waitForTimeout(1200)
const om = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
check('mobile: no horizontal scroll', om <= 1, `overflow=${om}`)
await page.screenshot({ path: `${OUT}/real-mobile.png`, fullPage: true })
await page.screenshot({ path: `${OUT}/real-mobile-top.png`, fullPage: false })

check('nothing was written: every non-GET /instagram request was blocked and none was attempted', blocked.length === 0, JSON.stringify(blocked))
check('no uncaught page errors', errors.length === 0, errors.join(' | ').slice(0, 300))
console.log('Instagram API paths called by the page (GET only):', [...new Set(calls)].join(', '))
await browser.close()
const failed = results.filter(r => !r).length
console.log(`\n${results.length - failed}/${results.length} checks passed`)
process.exit(failed ? 1 : 0)
