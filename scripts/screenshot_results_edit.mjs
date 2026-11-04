// Built during the Meta Global AI Developer Hackathon (new work).
// Headless-Chrome screenshots of /results and /edit/<id> at 1440 and 390 px wide, with every API call answered from fixtures
// (no backend, no key, no paid call). Usage: node scripts/screenshot_results_edit.mjs <vite base url> <out folder>
//   fixture result: samples/_local/result_final_e2e.json (a real stored analysis), plus a synthetic attention strip and Auto-edit state.
// Needs web/node_modules (playwright-core) and Google Chrome at the usual macOS path.
import { createRequire } from 'module'
import fs from 'fs'
const require = createRequire(new URL('../web/', import.meta.url).pathname)
const { chromium } = require('playwright-core')
const [, , BASE = 'http://localhost:5196', OUT = 'docs/screenshots'] = process.argv
fs.mkdirSync(OUT, { recursive: true })

const result = JSON.parse(fs.readFileSync('samples/_local/result_final_e2e.json', 'utf8')).result
const ID = result.analysis_id
const dur = 21.6, n = Math.round(dur / 0.5)
const wave = Array.from({ length: n }, (_, i) => +(0.35 + 0.45 * Math.abs(Math.sin(i / 3.1))).toFixed(2))
const quiet = [{ start: 4, end: 6.5, min_db: -51 }, { start: 17, end: 21, min_db: -49 }]
quiet.forEach(q => { for (let i = Math.round(q.start / 0.5); i < Math.round(q.end / 0.5); i++) wave[i] = 0.06 })
result.engagementPrediction = null
result.viewerAttention = {
  label: 'attention strip (estimate)', estimate: true, note: 'Estimates from the picture and the sound of this video, not measured viewer data.', bin_s: 0.5, duration: dur,
  visual_change: Array.from({ length: n }, (_, i) => +(0.3 + 0.5 * Math.abs(Math.cos(i / 4))).toFixed(2)), audio_energy: wave,
  audio_change: Array.from({ length: n }, (_, i) => +(0.2 + 0.3 * Math.abs(Math.sin(i / 2))).toFixed(2)),
  static_stretches: [{ start: 9, end: 12, duration: 3 }], loudness_dips: quiet, reasons: { visual_change: null, audio_energy: null, audio_change: null },
}
const ch = (id, type, risk, verdict, label, reason, extra = {}) => ({ id, type, group: ({ cut_segment: 'pacing', zoom_punch: 'zoom', caption_burn: 'text', text_overlay: 'text', loudnorm: 'audio' })[type],  label, reason, target_parameter: 'Retention & Pacing', enabled: verdict !== 'reverted', verdict, verdict_reasons: [], user_override: false, risk, ...extra })
const doneState = {
  status: 'done', phase: 'done', preset: 'clean', presets: ['clean', 'energetic', 'minimal'], running: false, error: null, cost: { total_usd: 0.41 },
  trace: Array.from({ length: 12 }, (_, i) => ({ t: String(i), kind: i % 5 === 0 ? 'kept' : 'info', msg: `activity log line ${i + 1}: planning, rendering, checking` })),
  changes: [
    ch('auto_silence1', 'cut_segment', 'safe', 'kept', 'Cut 3.2-4.1 s (dead air / stumble)', 'The gap after "so" is dead air.', { params: { start: 3.2, end: 4.1 } }),
    ch('auto_silence2', 'cut_segment', 'safe', 'kept', 'Cut 9.0-10.1 s (dead air / stumble)', 'Dead air before the last line.', { params: { start: 9.0, end: 10.1 } }),
    ch('auto_silence3', 'cut_segment', 'safe', 'kept', 'Cut 15.4-16.2 s (dead air / stumble)', 'A long breath pause.', { params: { start: 15.4, end: 16.2 } }),
    ch('mz1', 'zoom_punch', 'risky', 'reverted', 'Zoom punch at 6.2 s (×1.10)', 'The critic found the zoom cropped the face.', { params: { at: 6.2, scale: 1.1, duration: 0.25 }, verdict_reasons: ['Critic: the zoom cropped the speaker\'s forehead.'] }),
    ch('mz2', 'zoom_punch', 'risky', 'kept', 'Zoom punch at 12.0 s (×1.10)', 'Emphasis on the key word.', { params: { at: 12.0, scale: 1.1, duration: 0.25 } }),
    ch('auto_caps', 'caption_burn', 'safe', 'kept', 'Burned-in captions with word-by-word highlight', 'Most viewers watch with the sound off.'),
    ch('auto_loud', 'loudnorm', 'safe', 'kept', 'Loudness to -14 LUFS + light denoise', 'Instagram plays quieter videos lower.', { params: { target_lufs: -14 } }),
    ch('hook1', 'text_overlay', 'risky', 'kept', 'Hook text: “Wait for the last second”', 'The first frame had no text.', { params: { role: 'hook', text: 'Wait for the last second', start: 0, end: 2.8, anchor: 'output' } }),
    ch('lab1', 'text_overlay', 'risky', 'kept', 'Text: “Step 2”', 'Labels the second step.', { params: { role: 'label', text: 'Step 2', start: 11.0, end: 13.0, anchor: 'source' } }),
  ],
  groups: { zoom: { label: 'Zoom punches', status: 'kept', risk: 'risky', reasons: ['Visual +3'], target: 'Retention & Pacing', critic: { verdict: 'approve', reasons: [], defects: [] }, objective: { ok: true, checks: [{ name: 'face stays in frame', ok: true, detail: 'checked at 12.0 s' }] } }, pacing: { label: 'Pacing', status: 'kept', risk: 'safe', reasons: ['Tool checks passed'], target: 'Retention & Pacing', critic: { verdict: 'approve', reasons: [], defects: [] }, objective: { ok: true, checks: [{ name: 'duration', ok: true, detail: '30.0 s to 27.9 s' }] } } },
  manual_tips: Array.from({ length: 9 }, (_, i) => ({ parameter: 'Hook & Opening', tip: `Manual tip ${i + 1}: re-record the opening line with more energy and look at the lens.`, why_manual: 'needs a new take' })),
  revisions: [{ text: 'bigger captions', summary: ['captions 78 to 92'] }], revisions_left: 4,
  baseline: { metrics: { duration_s: 30.0, lufs: -21.4, true_peak_db: -3.2, silence_ratio: 0.18, cut_count: 0, avg_shot_s: 30 } },
  final: { metrics: { duration_s: 27.9, lufs: -14.1, true_peak_db: -1.4, silence_ratio: 0.04, cut_count: 0, avg_shot_s: 27.9 }, has_captions: true,
    overall_scores: { before: { 'Hook & Opening': 48, 'Retention & Pacing': 42, 'Audio & Music': 32, 'Trend Alignment': 45, 'On-Screen Text': 12 }, after: { 'Hook & Opening': 55, 'Retention & Pacing': 51, 'Audio & Music': 40, 'Trend Alignment': 46, 'On-Screen Text': 30 } } },
  packs: { clean: { label: 'Clean', headline: 'Poppins', caption: 'Poppins', accent: 'FFD43B', animation: 'fade', for: 'explainers' }, bold_hype: { label: 'Bold Hype', headline: 'Anton', caption: 'Montserrat', accent: 'FFE600', animation: 'pop', for: 'high energy' }, playful: { label: 'Playful', headline: 'Baloo 2', caption: 'Nunito', accent: 'FF6FB5', animation: 'bounce', for: 'lifestyle' }, editorial: { label: 'Editorial', headline: 'Playfair Display', caption: 'Inter', accent: 'E8C28A', animation: 'fade', for: 'fashion' } },
  intensities: ['subtle', 'balanced', 'punchy'], style: { pack: 'clean', font_headline: 'Poppins', font_caption: 'Poppins', intensity: 'balanced', sfx: true, pack_source: 'suggested' },
  style_choice: { pack: 'clean', pack_reason: 'a neutral default', fonts: [{ family: 'Anton', role: 'headline', reason: 'punchy' }, { family: 'Montserrat', role: 'caption', reason: 'readable' }], emphasis_count: 6, notes: [] },
  critic: { notes: 'no defects found in the kept changes' }, timing: { total_s: 142 }, clean_cut: { ready: true, ops: 4 },
}
const runningState = { ...doneState, status: 'running', running: true, phase: 'verify', final: null, changes: [], manual_tips: [], groups: {}, clean_cut: undefined }

const video = fs.readFileSync('samples/Video-57419.mp4')
const cover = Buffer.from(result.first_frame_b64, 'base64')
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
const user = { id: 'u1', email: 'shots@example.com', username: 'shots', plan: 'plus', credits: 50, is_verified: true, auth_provider: 'email' }

let closestBody = { connected: true, available: false, reason: 'No picture embedding.', note: 'x', ranked: [] }
let editState = doneState
async function mockApi(page) {
  await page.route(/\/(auth|prysm|instagram|api)\//, async route => {
    const u = new URL(route.request().url()), p = u.pathname
    if (p.endsWith('/auth/me')) return json(route, user)
    if (p.endsWith('/auto-edit/available')) return json(route, { enabled: true })
    if (p.endsWith('/status')) return json(route, { eligible: true, reason: '', state: editState })
    if (p.includes('/download/cover')) return route.fulfill({ status: 200, contentType: 'image/jpeg', body: cover })
    if (p.includes('/download/')) return route.fulfill({ status: 200, contentType: 'video/mp4', body: video })
    if (p.endsWith('/auto-edit/fonts')) return json(route, { fonts: [] })
    if (p.endsWith('/instagram/measured')) return json(route, { connected: true, isFixture: false, reels: 3, avgWatchThrough: 0.23, watchThroughReels: 3, partialReels: 0, avgSkipRate: 63, medianReach: 615 })
    if (p.includes('/instagram/closest/')) return json(route, closestBody)
    if (p.includes('/fixture-thumb/')) return route.fulfill({ status: 200, contentType: 'image/jpeg', body: cover })
    return json(route, {}, 404)
  })
}

let failed = 0
const check = (name, ok, extra = '') => { if (!ok) failed++; console.log(ok ? 'PASS' : 'FAIL', name, extra) }
const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true })
for (const [label, vp] of [['1440', { width: 1440, height: 900 }], ['390', { width: 390, height: 844 }]]) {
  const ctx = await browser.newContext({ userAgent: UA, viewport: vp, deviceScaleFactor: label === '390' ? 2 : 1 })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(String(e)))
  await mockApi(page)
  await page.goto(`${BASE}/login`)
  await page.evaluate(([res, u]) => {
    localStorage.setItem('prysm_token', 'fixture'); localStorage.setItem('prysm_refresh', 'fixture'); localStorage.setItem('prysm_user', JSON.stringify(u))
    localStorage.setItem('prysm_result', JSON.stringify(res)); localStorage.setItem('prysm_video_source', 'none')
  }, [result, user])

  // ---- /results (first without a picture comparison, then with one)
  editState = doneState
  await page.goto(`${BASE}/results`)
  await page.waitForSelector('[data-testid=attention-strip]', { timeout: 20000 })
  await page.waitForSelector('[data-testid=auto-edit-card]', { timeout: 20000 })
  await page.waitForTimeout(1500)
  check(`${label}: results has no Closest reel card when comparison is unavailable`, (await page.locator('[data-testid=closest-reel]').count()) === 0 && !(await page.textContent('body')).includes('Picture comparison is not available'))
  const panel = (await page.textContent('body'))
  check(`${label}: the quiet dips come from one source (two bands, 4-6.5 s and 17-21 s, in the sentence)`, panel.includes('Sound dips at 4-6.5s and 17-21s') && (await page.locator('[data-testid=mark-quiet]').count()) === 2)
  check(`${label}: Viewer behaviour title, three tiles and plain labels`, panel.includes('Viewer behaviour') && (await page.locator('[data-testid=m-tiles] > div').count()) === 3 && ['Usually watched', 'Skipped early', 'Typical reach', 'small sample'].every(t => panel.includes(t)) && (await page.locator('[data-testid=m-watched]').textContent()).includes('23') && (await page.locator('[data-testid=m-reach]').textContent()).includes('615'))
  check(`${label}: compact Auto-edit card`, (await page.locator('[data-testid=auto-edit-card]').textContent()).includes('Open Auto-edit studio'))
  const rOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  console.log(`INFO ${label}: /results page overflow=${rOverflow}px`)
  check(`${label}: no sparkline panel and no bar rows (one rhythm chart)`, (await page.locator('text=Audio Energy').count()) === 0 && (await page.locator('[data-testid=rhythm-chart]').count()) === 1 && (await page.locator('[data-testid^=row-]').count()) === 0)
  const card = page.locator('[data-testid=auto-edit-card]')
  const cb = await card.boundingBox(), vw = vp.width
  check(`${label}: Auto-edit card fully visible`, cb && cb.x >= 0 && cb.x + cb.width <= vw + 0.5 && (await card.locator('a').boundingBox()).x + (await card.locator('a').boundingBox()).width <= cb.x + cb.width + 0.5)
  check(`${label}: card shows three numbers`, (await card.locator('[data-testid=ae-numbers]').textContent()).includes('Pauses'))
  // with a picture comparison: thumbnail, similarity bar, others folded
  closestBody = { connected: true, available: true, isFixture: true, note: 'Looks similar is about pictures, not results.', own_reels: 3, compared_with: 3, missing_embeddings: 0, small_sample: true, small_sample_note: 'You have fewer than 3 imported reels, so this ranking is a small sample.',
    ranked: [1, 2, 3].map((n, i) => ({ n, thumbnailUrl: `/instagram/fixture-thumb/${n}`, similarity: [0.91, 0.55, 0.2][i], band: ['looks very alike', 'looks somewhat alike', 'looks quite different'][i], permalink: null, postedAt: null, partial: false, hookWords: i === 0 ? 'Stop scrolling' : null, lines: ['Reached 2,000 accounts (40% below your usual)', 'Viewers watched on average 30% of it'] })) }
  await page.reload()
  await page.waitForSelector('[data-testid=closest-reel]', { timeout: 20000 })
  await page.waitForTimeout(1200)
  check(`${label}: closest reel shows thumbnail + similarity bar, others folded`, (await page.locator('[data-testid=closest-thumb] img').count()) === 1 && (await page.locator('[data-testid=closest-bar]').count()) === 1 && !(await page.locator('[data-testid=closest-others]').evaluate(e => e.open)))
  console.log(`INFO ${label}: /results (with closest) overflow=${await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)}px`)
  await page.screenshot({ path: `${OUT}/results-${label}.png`, fullPage: true })
  await page.locator('[data-testid=viewer-behaviour]').scrollIntoViewIfNeeded()
  await page.locator('[data-testid=viewer-behaviour]').screenshot({ path: `${OUT}/results-${label}-viewer.png` })
  await page.locator('[data-testid=rhythm-chart] [role=img]').hover({ position: { x: 150, y: 60 } })
  await page.locator('[data-testid=viewer-behaviour]').screenshot({ path: `${OUT}/results-${label}-viewer-hover.png` })
  closestBody = { connected: true, available: false, reason: 'x', note: 'x', ranked: [] }

  // ---- /edit/<id>: finished edit, then a run in progress
  for (const [tag, state] of [['', doneState], ['-running', runningState]]) {
    editState = state
    await page.goto(`${BASE}/edit/${ID}`)
    await page.waitForSelector(state.running ? '[data-testid=stepper]' : '[data-testid=finish]', { timeout: 20000 })
    await page.waitForTimeout(1200)
    if (!state.running) {
      const body = await page.textContent('body')
      check(`${label}: edit page sections`, ['Pauses tightened', 'Zoom punches', 'Captions', 'Loudness and noise', 'Hook text', 'Labels', 'Ask for changes', 'Verified', 'Do it yourself', 'Finish in Instagram Edits', 'Direct Edits integration is not available yet', 'Recipe for Edits'].every(t => body.includes(t)))
      check(`${label}: six change groups`, (await page.locator('[data-testid=change-group]').count()) === 6)
      check(`${label}: summary tiles`, (await page.locator('[data-testid=summary] > div').count()) === 4 && (await page.locator('[data-testid=t-cost]').textContent()).includes('$0.41'))
      const pb = await page.locator('[data-testid=edit-left] video').first().boundingBox()
      if (label === '1440') check(`${label}: each player at least 260px wide`, pb && pb.width >= 260, `${Math.round(pb?.width || 0)}px`)
      await page.locator('[data-testid=change-group]').first().locator('summary').click()
      await page.locator('[data-testid=change-group]').nth(3).locator('summary').click()
      check(`${label}: hard cuts row only inside Details`, !(await page.locator('[data-testid=verified-details]').evaluate(e => e.open)))
      const resc = await page.textContent('[data-testid=rescore]')
      check(`${label}: re-score starts from the report's scores`, resc.includes('62') && !/\b(48|42|32|45)\b/.test(resc.replace(/Trend Alignment/, '')))
    }
    await page.waitForTimeout(300)
    const ov = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    check(`${label}: /edit${tag} has no horizontal overflow`, ov <= 0, `${ov}px`)
    await page.screenshot({ path: `${OUT}/edit${tag}-${label}.png`, fullPage: true })
  }
  check(`${label}: no page errors`, errors.length === 0, errors.join(' | ').slice(0, 200))
  await ctx.close()
}
await browser.close()
console.log(failed ? `${failed} CHECK(S) FAILED` : 'ALL CHECKS PASSED')
process.exit(failed ? 1 : 0)
