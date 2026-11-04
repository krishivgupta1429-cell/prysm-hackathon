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
const ch = (id, type, risk, verdict, label, reason, extra = {}) => ({ id, type, group: 'pacing', label, reason, target_parameter: 'Retention & Pacing', enabled: verdict !== 'reverted', verdict, verdict_reasons: [], user_override: false, risk, ...extra })
const doneState = {
  status: 'done', phase: 'done', preset: 'clean', presets: ['clean', 'energetic', 'minimal'], running: false, error: null, cost: { total_usd: 0.41 },
  trace: Array.from({ length: 12 }, (_, i) => ({ t: String(i), kind: i % 5 === 0 ? 'kept' : 'info', msg: `activity log line ${i + 1}: planning, rendering, checking` })),
  changes: [
    ch('auto_silence1', 'cut_segment', 'safe', 'kept', 'Tighten a pause: cut 0.9 s of silence', 'The gap after "so" is dead air.'),
    ch('auto_silence2', 'cut_segment', 'safe', 'kept', 'Tighten a pause: cut 1.1 s of silence', 'Dead air before the last line.'),
    ch('auto_loud', 'loudnorm', 'safe', 'kept', 'Normalise loudness to -14 LUFS', 'Instagram plays quieter videos lower.'),
    ch('auto_caps', 'caption_burn', 'safe', 'kept', 'Burn in captions', 'Most viewers watch with the sound off.'),
    ch('mz1', 'zoom_punch', 'risky', 'reverted', 'Zoom punch at 6.2 s', 'The critic found the zoom cropped the face.', { verdict_reasons: ['Critic: the zoom cropped the speaker\'s forehead.'] }),
    ch('hook1', 'text_overlay', 'risky', 'kept', 'Hook text: "Wait for the last second"', 'The first frame had no text.'),
  ],
  groups: { pacing: { label: 'Pacing', status: 'kept', risk: 'safe', reasons: ['Tool checks passed'], target: 'Retention & Pacing', critic: { verdict: 'approve', reasons: [], defects: [] }, objective: { ok: true, checks: [{ name: 'duration', ok: true, detail: '30.0 s to 27.9 s' }] } } },
  manual_tips: Array.from({ length: 9 }, (_, i) => ({ parameter: 'Hook & Opening', tip: `Manual tip ${i + 1}: re-record the opening line with more energy and look at the lens.`, why_manual: 'needs a new take' })),
  revisions: [{ text: 'bigger captions', summary: ['captions 78 to 92'] }], revisions_left: 4,
  baseline: { metrics: { duration_s: 30.0, lufs: -21.4, true_peak_db: -3.2, silence_ratio: 0.18, cut_count: 0, avg_shot_s: 30 } },
  final: { metrics: { duration_s: 27.9, lufs: -14.1, true_peak_db: -1.4, silence_ratio: 0.04, cut_count: 0, avg_shot_s: 27.9 }, has_captions: true,
    overall_scores: { before: { 'Hook & Opening': 48, 'Retention & Pacing': 42, 'Audio & Music': 32, 'Trend Alignment': 45, 'On-Screen Text': 12 }, after: { 'Hook & Opening': 55, 'Retention & Pacing': 51, 'Audio & Music': 40, 'Trend Alignment': 46, 'On-Screen Text': 30 } } },
  packs: { clean: { label: 'Clean', headline: 'Poppins', caption: 'Poppins', accent: 'FFD43B', animation: 'fade', for: 'explainers' }, bold_hype: { label: 'Bold Hype', headline: 'Anton', caption: 'Montserrat', accent: 'FFE600', animation: 'pop', for: 'high energy' }, playful: { label: 'Playful', headline: 'Baloo 2', caption: 'Nunito', accent: 'FF6FB5', animation: 'bounce', for: 'lifestyle' }, editorial: { label: 'Editorial', headline: 'Playfair Display', caption: 'Inter', accent: 'E8C28A', animation: 'fade', for: 'fashion' } },
  intensities: ['subtle', 'balanced', 'punchy'], style: { pack: 'clean', font_headline: 'Poppins', font_caption: 'Poppins', intensity: 'balanced', sfx: true, pack_source: 'suggested' },
  style_choice: { pack: 'clean', pack_reason: 'a neutral default', fonts: [{ family: 'Anton', role: 'headline', reason: 'punchy' }, { family: 'Montserrat', role: 'caption', reason: 'readable' }], emphasis_count: 6, notes: [] },
  critic: { notes: 'no defects found in the kept changes' }, timing: { total_s: 142 },
}
const runningState = { ...doneState, status: 'running', running: true, phase: 'verify', final: null, changes: [], manual_tips: [], groups: {} }

const video = fs.readFileSync('samples/Video-57419.mp4')
const cover = Buffer.from(result.first_frame_b64, 'base64')
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
const user = { id: 'u1', email: 'shots@example.com', username: 'shots', plan: 'plus', credits: 50, is_verified: true, auth_provider: 'email' }

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
    if (p.includes('/instagram/closest/')) return json(route, { connected: true, available: false, reason: 'No picture embedding.', note: 'x', ranked: [] })
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

  // ---- /results
  editState = doneState
  await page.goto(`${BASE}/results`)
  await page.waitForSelector('[data-testid=attention-strip]', { timeout: 20000 })
  await page.waitForSelector('[data-testid=auto-edit-card]', { timeout: 20000 })
  await page.waitForTimeout(1500)
  check(`${label}: results has no Closest reel card when comparison is unavailable`, (await page.locator('[data-testid=closest-reel]').count()) === 0 && !(await page.textContent('body')).includes('Picture comparison is not available'))
  const panel = (await page.textContent('body'))
  check(`${label}: audio panel and strip agree (panel flags 4-6.5 s, not the old 0-3 s)`, panel.includes('Quiet 4s–6.5s') && !panel.includes('Quiet 0s–3s'))
  check(`${label}: Viewer behaviour title and plain labels`, panel.includes('Viewer behaviour') && panel.includes('Usually watched') && panel.includes('23% of a reel') && panel.includes('Skipped early') && panel.includes('615 accounts') && panel.includes('small sample'))
  check(`${label}: compact Auto-edit card`, (await page.locator('[data-testid=auto-edit-card]').textContent()).includes('Open Auto-edit studio'))
  const rOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  console.log(`INFO ${label}: /results page overflow=${rOverflow}px`)
  await page.screenshot({ path: `${OUT}/results-${label}.png`, fullPage: true })
  await page.locator('[data-testid=viewer-behaviour]').scrollIntoViewIfNeeded()
  await page.getByText('Show details').first().click()
  await page.locator('[data-testid=viewer-behaviour]').screenshot({ path: `${OUT}/results-${label}-viewer-details.png` })

  // ---- /edit/<id>: finished edit, then a run in progress
  for (const [tag, state] of [['', doneState], ['-running', runningState]]) {
    editState = state
    await page.goto(`${BASE}/edit/${ID}`)
    await page.waitForSelector(state.running ? '[data-testid=stepper]' : '[data-testid=export]', { timeout: 20000 })
    await page.waitForTimeout(1200)
    if (!state.running) {
      const body = await page.textContent('body')
      check(`${label}: edit page sections`, ['Safe fixes', 'Taste calls', 'Ask for changes', 'Verified', 'Do it yourself', 'Export for Instagram Edits', 'Hard cuts (shot changes)', 'Pauses removed'].every(t => body.includes(t)))
      const resc = await page.textContent('[data-testid=rescore]')
      check(`${label}: re-score starts from the report's scores`, resc.includes('62') && !/\b(48|42|32|45)\b/.test(resc.replace(/Trend Alignment/, '')))
    }
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
