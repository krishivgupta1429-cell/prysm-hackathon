// Built during the Meta Global AI Developer Hackathon (new work).
// Headless-Chrome check of the whole Creator DNA flow in fixture mode (local stack: web 5177, backend 8778, INSTAGRAM_FIXTURE=1, a verified
// test user ig.tester@example.org). Needs web/node_modules (playwright-core). Usage: node scripts/e2e_instagram_fixture.mjs <analysis result.json>
import { createRequire } from 'module'
import fs from 'fs'
import { execFileSync } from 'child_process'
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

// 0. start clean (this script only ever touches its own test user)
await api('/instagram/disconnect', 'DELETE')
const shot = async (name, full = true) => page.screenshot({ path: `${OUT}/${name}.png`, fullPage: full })
const noOverflow = async (label) => { const o = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth); check(`${label}: no horizontal scroll`, o <= 1, `overflow=${o}`) }

// 1. consent screen
await page.goto(`${BASE}/creator-dna`)
await page.waitForSelector('[data-testid=consent]')
const consent = await page.textContent('[data-testid=consent]')
check('consent: headline, one value sentence, three benefits, one button, one muted line',
  /See what actually works in your own reels, based on the numbers Instagram measured\./.test(consent) && (await page.locator('[data-testid=benefits] li').count()) === 3
  && (await page.locator('[data-testid=consent] button').count()) === 1 && /Read-only access\. Disconnect anytime\./.test(consent))
check('consent: the old privacy bullets and the account note are gone',
  !/What we read|What we store|Who processes|Deleting it|Permissions asked|downloaded for a moment|Business or Creator Instagram account|I understand|checkbox/i.test(consent))
await shot('01-consent')

// 2. connect (fixture): account card, import card, limits
await page.click('[data-testid=connect]')
await page.waitForSelector('[data-testid=import-card]')
check('fixture banner shown', await page.isVisible('[data-testid=fixture-banner]'))
check('account card: handle, connected, nothing imported yet', /@fixture_creator/.test(await page.textContent('[data-testid=handle]')) && /Connected/.test(await page.textContent('[data-testid=connected-status]')) && /Nothing imported yet/.test(await page.textContent('[data-testid=last-imported]')))
check('empty state before the first import', await page.isVisible('[data-testid=empty-import]'))
check('"You have 12 reels available" is shown', /You have 12 reels available/.test(await page.textContent('[data-testid=available]')))
check('default count is 10 (the fixture has 12)', (await page.inputValue('[data-testid=count-input]')) === '10')
check('slider minimum is 1 and maximum is the number available', (await page.getAttribute('[data-testid=count]', 'min')) === '1' && (await page.getAttribute('[data-testid=count]', 'max')) === '12')
await page.fill('[data-testid=count-input]', '50')
check('typing a number above the maximum is capped at 12', (await page.inputValue('[data-testid=count-input]')) === '12')
await page.fill('[data-testid=count-input]', '1')
check('typing 1 is allowed', (await page.inputValue('[data-testid=count-input]')) === '1' && /about \d+ seconds/.test(await page.textContent('[data-testid=estimate]')))
await shot('02-connected')

// 3. one reel first: breakdown only, no pattern claims
await page.click('[data-testid=import]')
await page.waitForSelector('[data-testid=import-result]', { timeout: 120000 })
await page.waitForSelector('[data-testid=breakdown-note]')
check('1 reel: "Patterns need at least 3 reels" note, no DNA card', await page.isVisible('[data-testid=breakdown-note]') && /Patterns need at least 3 reels/.test(await page.textContent('[data-testid=breakdown-note]')) && !(await page.isVisible('[data-testid=dna]')))
await page.waitForSelector('[data-testid=reel-row]')
check('1 reel: the per-reel breakdown is shown', (await page.$$('[data-testid=reel-row]')).length === 1)
await shot('03-one-reel')

// 4. eight reels with the stepper, progress, DNA
await page.fill('[data-testid=count-input]', '10')
await page.click('[aria-label="fewer reels"]'); await page.click('[aria-label="fewer reels"]')
check('stepper to 8', (await page.inputValue('[data-testid=count-input]')) === '8')
await page.click('[data-testid=import]')
await page.waitForSelector('[data-testid=progress]', { timeout: 15000 })
check('progress state appears with per-reel steps', true)
await page.waitForSelector('[data-testid=progress] li', { timeout: 20000 })
check('per-reel progress chips', (await page.$$('[data-testid=progress] li')).length > 0)
await shot('04-progress', false)
await page.waitForSelector('[data-testid=import-result]', { timeout: 240000 })
const resText = await page.textContent('[data-testid=import-result]')
check('import result says 8 of 8', /Imported 8 of 8/.test(resText), resText.trim().slice(0, 160))
check('import result mentions the reel without a video file', /had no video file from Instagram/.test(resText) && /not fully analysed/.test(resText))
await page.waitForSelector('[data-testid=dna]')
check('DNA header says "Based on 8 reels"', /Based on 8 reels/.test(await page.textContent('[data-testid=dna-title]')))
check('last imported time shown on the account card', /Last imported (just now|\d+ minutes? ago)/.test(await page.textContent('[data-testid=last-imported]')))
const rows = await page.$$('[data-testid=reel-row]')
check('8 reel cards', rows.length === 8, String(rows.length))

// DNA: every claim once, each with cited reel chips; a chip opens that reel
const claims = await page.$$('[data-testid=claim]')
check('DNA claims each show cited reel chips', claims.length > 0 && (await Promise.all(claims.map(c => c.$$('[data-testid=reel-chips] button')))).every(b => b.length > 0), `claims=${claims.length}`)
const claimTexts = await page.$$eval('[data-testid=claim]', els => els.map(e => e.textContent.replace(/Reel \d+/g, '').trim().toLowerCase()))
check('no claim is repeated', new Set(claimTexts).size === claimTexts.length)
const dnaText = await page.textContent('[data-testid=dna]')
check('DNA does not repeat a prose summary', !/Based on your 8 most recent imported reels/.test(dnaText) && (dnaText.match(/not proof/g) || []).length === 1)
await page.click('[data-testid=claim] [data-testid=reel-chips] button')
await page.waitForSelector('[data-testid=reel-dialog]')
check('a reel chip opens that reel', /^Reel \d+/.test(await page.textContent('[data-testid=reel-dialog] h2')) && await page.isVisible('[data-testid=reel-dialog] [data-testid=metrics]'))
await shot('05-reel-dialog', false)
await page.keyboard.press('Escape')
await page.waitForSelector('[data-testid=reel-dialog]', { state: 'detached' })

// reel cards: thumbnail, caption at a word boundary, plain language, real metrics, badge
const reelsText = await page.textContent('[data-testid=reels]')
check('plain-language reach: "% above/below your usual", no index numbers', /Reach \d+% (above|below) your usual|about your usual/.test(reelsText) && !/index \d\.\d\d/.test(reelsText))
check('the "your usual" note is shown once', (await page.$$('[data-testid=vs-note]')).length === 1)
for (const img of await page.$$('[data-testid=reel-row] img')) await img.scrollIntoViewIfNeeded()   // thumbnails load lazily
await page.waitForTimeout(1500)
check('every reel card shows a thumbnail that loaded', (await page.$$eval('[data-testid=reel-row] img', els => els.map(i => i.complete && i.naturalWidth > 0))).filter(Boolean).length >= 7)
await page.evaluate(() => window.scrollTo(0, 0))
check('metrics show real numbers and "no data yet" instead of 0', /Views/.test(reelsText) && /Saves/.test(reelsText) && /Avg watch time/.test(reelsText) && /no data yet/.test(reelsText) && !/Skip rate\s*0%/.test(reelsText))
check('hook words line on every reel', (await page.$$('[data-testid=hook-words]')).length === 8)
const hookTexts = await page.$$eval('[data-testid=hook-words]', els => els.map(e => e.textContent.replace(/\s+/g, ' ')))
check('talking reels show their hook words in quotes; b-roll says no speech; the video-less reel says unknown (no video file)',
  hookTexts.some(t => /Hook words: .?Why do your reels flop/.test(t)) && hookTexts.some(t => /no speech in the first seconds/.test(t)) && hookTexts.some(t => /unknown \(no video file from Instagram\)/.test(t)), hookTexts.join(' | ').slice(0, 200))
check('exactly one reel carries the thumbnail-only badge', (await page.$$('[data-testid=partial-badge]')).length === 1)
const partialCard = await page.$('[data-testid=reel-row]:has([data-testid=partial-badge])')
const partialText = (await partialCard.textContent()).replace(/\s+/g, ' ')
check('that reel: not fully analysed, no watch-through, reason given', /Thumbnail \+ caption only/.test(partialText) && /not fully analysed/.test(partialText) && /none \(no video\)/.test(partialText) && /no video file/.test(partialText))
check('that reel shows no hook / format / pacing chips', !/(question|bold claim|list promise|visual only|talking head|montage|fast pace|slow pace|medium pace)/i.test(partialText.replace(/Hook words.*?(?=What Instagram)/, '')))
const pn = (await page.textContent('[data-testid=partial-notice]')).replace(/\s+/g, ' ')
check('DNA card: "Not fully analysed" notice names the reel', /Not fully analysed: Reel \d+/.test(pn) && /hook, format and pacing were not judged/.test(pn))
check('reels list counts fully analysed vs thumbnail-only', /7 of 8 reels fully analysed from video; 1 from thumbnail and caption only/.test(await page.textContent('[data-testid=analysed-count]')))
// captions: cut at a word, full caption on request
const longCard = await page.$('[data-testid=reel-row]:has([data-testid=caption-toggle])')
if (longCard) {
  const cut = (await (await longCard.$('[data-testid=caption]')).textContent()).trim()
  await (await longCard.$('[data-testid=caption-toggle]')).click()
  const full = (await (await longCard.$('[data-testid=caption]')).textContent()).trim()
  check('"Show full caption" reveals the whole caption', full.length > cut.length && !full.endsWith('…'))
}
await shot('06-imported')

// 4b. "Did my fix work?": a saved analysis (inserted for the test user, 14 days old, 22 s long) linked to the reel posted after it
const PSQL = process.env.E2E_PSQL || '/opt/homebrew/opt/postgresql@18/bin/psql'
const psql = (sql) => execFileSync(PSQL, ['-h', '127.0.0.1', '-p', process.env.E2E_PGPORT || '55436', '-U', 'prysm', process.env.E2E_PGDB || 'prysm_ig', '-Atc', sql]).toString().trim()
psql("DELETE FROM prysm_analyses WHERE id = '00000000-0000-4000-8000-0000000000e2'")
psql(`INSERT INTO prysm_analyses (id, user_id, created_at, overall_score, summary, top_priorities, parameters, raw_result, intent, is_public)
  SELECT '00000000-0000-4000-8000-0000000000e2', id, now() - interval '9 days', 58, 'Strong angle, slow start', '["Open on the result in the first second","Add a re-hook at 9 seconds"]'::json,
  '[{"name":"Hook","score":40,"fix":"Use a question hook"}]'::json, '{"energyCurve":{"duration":22.0}}'::json, 'my_video', false FROM users WHERE email = 'ig.tester@example.org'`)
await page.reload(); await page.waitForSelector('[data-testid=fix-loop]')
check('"Did my fix work?" section is shown with an open button', await page.isVisible('[data-testid=fix-open]'))
await page.click('[data-testid=fix-open]')
await page.selectOption('[data-testid=fix-analysis]', '00000000-0000-4000-8000-0000000000e2')
check('the likely reel is suggested by date and length', /Reel 2 .*likely match/.test(await page.$eval('[data-testid=fix-reel]', el => el.selectedOptions[0].textContent)) && /length matches/.test(await page.textContent('[data-testid=fix-why]')))
await page.check('text=Open on the result in the first second')
await shot('08a-fix-form', false)
await page.click('[data-testid=fix-save]')
await page.waitForSelector('[data-testid=fix-result]')
const fx = (await page.textContent('[data-testid=fix-loop]')).replace(/\s+/g, ' ')
check('result: compared with your other 7 reels, in plain language', /compared with your other 7 reels/.test(fx) && /Reach \d+% (above|below) your usual|Reach about your usual/.test(fx))
check('result: watch-through, saves, shares per reach and skip rate rows', /Watch-through/.test(fx) && /Saves per 1,000 reached/.test(fx) && /Shares per 1,000 reached/.test(fx) && /Skip rate/.test(fx))
check('result: names the advice applied', /Advice you applied/.test(fx) && /Open on the result in the first second/.test(fx) && !/Add a re-hook at 9 seconds/.test(fx))
check('result: honest copy (lag, one reel is not proof, based on N reels)', /48 hours/.test(fx) && /not proof/.test(fx) && /median of your other 7/.test(fx) && !/it worked|fix worked/i.test(fx))
await shot('08b-fix-result')
await page.click('[data-testid=fix-unlink]')
await page.waitForSelector('[data-testid=fix-link]', { state: 'detached' })
check('a link can be removed', (await (await api('/instagram/links')).json()).links.length === 0)
await page.click('[data-testid=fix-open]'); await page.selectOption('[data-testid=fix-analysis]', '00000000-0000-4000-8000-0000000000e2')
await page.click('[data-testid=fix-save]'); await page.waitForSelector('[data-testid=fix-result]')   // linked again: used by the disconnect check below

// 5. keyboard: tab reaches the account menu and the slider; menu opens with Enter
await page.keyboard.press('Home')
await page.focus('[data-testid=account-menu]')
await page.keyboard.press('Enter')
check('account menu opens from the keyboard', await page.isVisible('[data-testid=menu-disconnect]'))
await page.keyboard.press('Escape')
check('slider is reachable and keyboard-adjustable', await page.evaluate(() => { const r = document.querySelector('[data-testid=count]'); r.focus(); return document.activeElement === r }))

// 6. mobile layout
await page.setViewportSize({ width: 390, height: 844 })
await page.reload(); await page.waitForSelector('[data-testid=dna]')
await noOverflow('mobile')
await shot('07-mobile')
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
check('comparison card: plain-language reach for the cited reels, no index numbers', /Reach \d+% (above|below) your usual|Reach about your usual/.test(card) && !/index \d\.\d\d/.test(card), card.replace(/\s+/g, ' ').slice(0, 160))
check('comparison card: shows the first words of the new video and of the cited reels', await page.isVisible('[data-testid=new-hook-words]') && (await page.$$('[data-testid=cited-reel]')).length >= 2 && /Opens with|Opening words unknown/.test(card))
if (result.creatorDna.partialReels && result.creatorDna.partialReels.length) check('comparison card: says which reels were read without video', /read from the thumbnail and caption only/.test(await page.textContent('[data-testid=partial-line]')))
check('results: grounding stats saved with the analysis', !!result.creatorDna.grounding && typeof result.creatorDna.grounding.checked === 'number', JSON.stringify(result.creatorDna.grounding))
await page.screenshot({ path: `${OUT}/06-results-personalised.png`, fullPage: false })
const el = await page.$('[data-testid=viewer-behaviour]'); await el.scrollIntoViewIfNeeded()
await page.screenshot({ path: `${OUT}/07-results-viewer-behaviour.png` })
const elc = await page.$('[data-testid=creator-dna-card]'); await elc.scrollIntoViewIfNeeded()
await page.screenshot({ path: `${OUT}/08-results-compare.png` })

// 8. disconnect from the account menu, with a confirm dialog
await page.goto(`${BASE}/creator-dna`)
await page.waitForSelector('[data-testid=account-menu]')
await page.click('[data-testid=account-menu]')
await page.click('[data-testid=menu-disconnect]')
await page.waitForSelector('[data-testid=disconnect-dialog]')
check('disconnect asks for confirmation first', /Disconnect Instagram\?/.test(await page.textContent('[data-testid=disconnect-dialog]')))
await shot('09-disconnect-dialog', false)
await page.keyboard.press('Escape')
check('cancelling keeps the account connected', (await (await api('/instagram/dna')).json()).connected === true)
await page.click('[data-testid=account-menu]'); await page.click('[data-testid=menu-disconnect]')
await page.click('[data-testid=disconnect-confirm]')
await page.waitForSelector('[data-testid=consent]')
check('after disconnect: consent screen again', true)
const dna = await (await api('/instagram/dna')).json()
check('after disconnect: API has no DNA or reels', dna.connected === false)
check('after disconnect: the analysis link is deleted too', psql("SELECT count(*) FROM ig_fix_link WHERE analysis_id = '00000000-0000-4000-8000-0000000000e2'") === '0')
psql("DELETE FROM prysm_analyses WHERE id = '00000000-0000-4000-8000-0000000000e2'")
await shot('10-disconnected')

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
