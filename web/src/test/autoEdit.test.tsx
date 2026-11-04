// Built during the Meta Global AI Developer Hackathon (new work).
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const calls: string[] = []
let statusBody: unknown = null

vi.mock('@/lib/auth', () => ({
  isLoggedIn: () => true,
  getToken: () => 'tok',
  authFetch: vi.fn(async (url: string) => {
    calls.push(url)
    if (url.endsWith('/auto-edit/available')) return new Response(JSON.stringify({ enabled: true }), { status: 200 })
    if (url.endsWith('/status')) return new Response(JSON.stringify(statusBody), { status: 200 })
    if (url.endsWith('/toggle')) return new Response(JSON.stringify((statusBody as { state: unknown }).state), { status: 200 })
    if (url.includes('/download/')) return new Response(new Blob(['x']), { status: 200 })
    return new Response('{}', { status: 200 })
  }),
}))

import Edit from '@/pages/Edit'
import AutoEditCard from '@/studio/AutoEditCard'
import { authFetch } from '@/lib/auth'

const chg = (id: string, type: string, risk: string, verdict: string, label: string, extra: object = {}) =>
  ({ id, type, group: type === 'cut_segment' ? 'pacing' : type, label, reason: `${label} reason`, target_parameter: '', enabled: verdict !== 'reverted', verdict, verdict_reasons: [], user_override: false, risk, ...extra })

const done = {
  eligible: true, reason: '', state: {
    status: 'done', phase: 'done', preset: 'clean', presets: ['clean', 'energetic', 'minimal'], running: false, cost: { total_usd: 0.41 }, trace: [{ t: '0', kind: 'info', msg: 'raw log line' }],
    changes: [
      chg('auto_silence1', 'cut_segment', 'safe', 'kept', 'Tighten a pause', { params: { start: 1.0, end: 1.9 } }),
      chg('auto_silence2', 'cut_segment', 'safe', 'kept', 'Tighten another pause', { params: { start: 5.0, end: 6.1 } }),
      chg('mz1', 'zoom_punch', 'risky', 'reverted', 'Zoom punch', { params: { at: 6.2, scale: 1.1 } }),
      chg('caps', 'caption_burn', 'safe', 'kept', 'Burned-in captions'),
      chg('loud', 'loudnorm', 'safe', 'kept', 'Loudness to -14 LUFS', { params: { target_lufs: -14 } }),
      chg('hook1', 'text_overlay', 'risky', 'kept', 'Hook text: “Wait for the last second”', { params: { role: 'hook', text: 'Wait for the last second', start: 0, end: 2.8, anchor: 'output' } }),
    ],
    groups: { pacing: { label: 'Pauses', status: 'kept', risk: 'safe', reasons: [], target: 'x', critic: { verdict: 'approve', reasons: [], defects: [] }, objective: { ok: true, checks: [{ name: 'duration', ok: true, detail: '30 to 27' }] } } }, manual_tips: Array.from({ length: 8 }, (_, i) => ({ parameter: 'Hook', tip: `tip number ${i + 1}` })), revisions: [], revisions_left: 5,
    baseline: { metrics: { duration_s: 30, cut_count: 0, silence_ratio: 0.2 } },
    final: { metrics: { duration_s: 27, cut_count: 0, silence_ratio: 0.05 }, has_captions: true,
      overall_scores: { before: { 'Hook & Opening': 48, 'Retention & Pacing': 42 }, after: { 'Hook & Opening': 50, 'Retention & Pacing': 47 } } },
    packs: { clean: { label: 'Clean', headline: 'Poppins', caption: 'Poppins', accent: 'FFD43B', animation: 'fade', for: 'x' }, bold_hype: { label: 'Bold Hype', headline: 'Anton', caption: 'Montserrat', accent: 'FFE600', animation: 'pop', for: 'y' } },
    intensities: ['subtle', 'balanced', 'punchy'], style: { pack: 'clean', font_headline: 'Poppins', font_caption: 'Poppins', intensity: 'balanced', sfx: true, pack_source: 'suggested' },
    style_choice: { pack: 'clean', pack_reason: 'neutral', fonts: [], emphasis_count: 3, notes: [] },
  },
}

function mount(path = '/edit/abc') {
  return render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/edit/:analysisId" element={<Edit />} /><Route path="/results" element={<p>results</p>} /></Routes></MemoryRouter>)
}

beforeEach(() => {
  calls.length = 0
  localStorage.clear()
  localStorage.setItem('prysm_result', JSON.stringify({ analysis_id: 'abc', parameters: [{ name: 'Hook & Opening', score: 64 }, { name: 'Retention & Pacing', score: 52 }] }))
  Object.defineProperty(URL, 'createObjectURL', { value: () => 'blob:x', configurable: true })
  Object.defineProperty(URL, 'revokeObjectURL', { value: () => {}, configurable: true })
})
afterEach(() => vi.clearAllMocks())

describe('Auto-edit edit page', () => {
  it('shows the summary tiles, about six change groups with one chip each, exact times, and never lets the re-score contradict the report', async () => {
    statusBody = done
    mount()
    await screen.findByTestId('summary')
    // four before -> after tiles; the AI cost sits in the header
    expect(screen.getByTestId('t-length-delta').textContent).toContain('was 30.0')
    expect(screen.getByTestId('t-length-delta').textContent).toContain('−3.0')
    expect(screen.getByTestId('t-pauses').textContent).toContain('2')
    expect(screen.getByTestId('t-cost').textContent).toContain('AI cost $0.41')
    // changes grouped
    const groups = screen.getAllByTestId('change-group')
    expect(groups.map(g => g.getAttribute('data-key'))).toEqual(['pauses', 'zoom', 'captions', 'loudness', 'hook'])
    expect(screen.getAllByTestId('group-chip').map(c => c.textContent)).toEqual(['KEPT', 'REVERTED', 'KEPT', 'KEPT', 'KEPT'])
    const pauses = groups[0]
    expect(within(pauses).getAllByTestId('change-row')).toHaveLength(2)
    expect(within(pauses).getAllByTestId('change-when')[0].textContent).toBe('1.0-1.9 s of the original')
    for (const t of ['Changes', 'Style', 'Ask for changes', 'Verified', 'Do it yourself', 'Finish in Instagram Edits']) expect(screen.getAllByText(t).length).toBeGreaterThan(0)
    // the measured table is behind Details, with the renamed row and the pauses row
    const details = screen.getByTestId('verified-details')
    expect(details.hasAttribute('open')).toBe(false)
    const table = within(details).getByTestId('measured-table')
    expect(within(table).getByText('Hard cuts (shot changes)')).toBeTruthy()
    expect(within(table).getByText('Pauses removed').closest('tr')!.textContent).toContain('2')
    // the re-score starts from the REPORT's scores (64, 52), not the AI run's own 48 / 42
    const rescore = screen.getByTestId('rescore')
    expect(rescore.textContent).toContain('AI-estimated, informational')
    expect(within(rescore).getByText('Hook & Opening').closest('tr')!.textContent).toBe('Hook & Opening6466+2')
    expect(within(rescore).getByText('Retention & Pacing').closest('tr')!.textContent).toBe('Retention & Pacing5257+5')
    expect(rescore.textContent).not.toMatch(/\b48\b|\b42\b/)
    // do-it-yourself is collapsed with a count; 8 tips: 5 shown, "3 more" folded
    expect(screen.getByTestId('manual').hasAttribute('open')).toBe(false)
    expect(within(screen.getByTestId('manual')).getByTestId('section-count').textContent).toBe('8')
    expect(screen.getByText('3 more')).toBeTruthy()
    // Finish in Instagram Edits: says there is no integration, one clean-cut button, a recipe with exact times
    const finish = screen.getByTestId('finish')
    expect(finish.textContent).toContain('Direct Edits integration is not available yet')
    expect(screen.getByTestId('make-clean')).toBeTruthy()
    const recipe = screen.getByTestId('recipe').textContent || ''
    expect(recipe).toContain("Captions: switch on Edits' own auto-captions")
    expect(recipe).toContain('Hook text “Wait for the last second”: add a text layer from 0.0 to 2.8 s')
    expect(recipe).not.toContain('Zoom punch')                              // the zoom was reverted, so it is not in the recipe
    // everything went through authFetch
    expect(vi.mocked(authFetch).mock.calls.length).toBeGreaterThan(2)
  })

  it('toggling a whole group sends one request with every op id', async () => {
    statusBody = done
    mount()
    await screen.findByTestId('summary')
    const pauses = screen.getAllByTestId('change-group')[0]
    fireEvent.click(within(pauses).getByRole('switch', { name: 'Toggle all: Pauses tightened' }))
    await waitFor(() => expect(vi.mocked(authFetch).mock.calls.some(c => String(c[0]).endsWith('/toggle'))).toBe(true))
    const call = vi.mocked(authFetch).mock.calls.find(c => String(c[0]).endsWith('/toggle'))!
    expect(JSON.parse(String((call[1] as RequestInit).body))).toEqual({ op_ids: ['auto_silence1', 'auto_silence2'], enabled: false })
  })

  it('hides the re-score when the stored report is for another analysis', async () => {
    statusBody = done
    localStorage.setItem('prysm_result', JSON.stringify({ analysis_id: 'other', parameters: [{ name: 'Hook & Opening', score: 64 }] }))
    mount()
    await screen.findByTestId('summary')
    expect(screen.queryByTestId('rescore')).toBeNull()
    expect(screen.getByTestId('back-link').textContent).toContain('dashboard')
  })

  it('names the stage that is running in a stepper', async () => {
    statusBody = { eligible: true, reason: '', state: { ...done.state, running: true, status: 'running', phase: 'planning', final: null } }
    mount()
    const stepper = await screen.findByTestId('stepper')
    const now = Array.from(stepper.querySelectorAll('li')).find(li => li.getAttribute('data-state') === 'now')!
    expect(now.textContent).toContain('Planning')
    expect(screen.getByTestId('progress').getAttribute('aria-valuenow')).toBe('30')
    expect(Array.from(stepper.querySelectorAll('li')).filter(li => li.getAttribute('data-state') === 'done').map(li => li.textContent).join()).toContain('Reading your video')
  })

  it('says the rendered pack was not the suggested one when a request changed it', async () => {
    statusBody = { ...done, state: { ...done.state, style: { ...done.state.style, pack: 'bold_hype', pack_source: 'your choice' } } }
    mount()
    const note = await screen.findByTestId('pack-note')
    expect(note.textContent).toContain('Rendered with Bold Hype (your choice)')
    expect(note.textContent).toContain('suggested Clean')
  })
})

describe('Auto-edit card on the results page', () => {
  const card = () => render(<MemoryRouter><AutoEditCard analysisId="abc" /></MemoryRouter>)
  it('offers to start when no edit exists', async () => {
    statusBody = { eligible: true, reason: '', state: null }
    card()
    const link = await screen.findByRole('link', { name: 'Auto-edit this reel' })
    expect(link.getAttribute('href')).toBe('/edit/abc')
  })
  it('shows the number of kept changes and opens the edit page when an edit exists', async () => {
    statusBody = done
    card()
    const link = await screen.findByRole('link', { name: 'Open Auto-edit studio' })
    expect(link.getAttribute('href')).toBe('/edit/abc')
    await waitFor(() => expect(screen.getByTestId('auto-edit-card-line').textContent).toContain('5 changes kept'))
  })
  it('renders nothing when the analysis cannot be edited and has no edit', async () => {
    statusBody = { eligible: false, reason: 'Links cannot be edited.', state: null }
    const { container } = card()
    await waitFor(() => expect(vi.mocked(authFetch).mock.calls.some(c => String(c[0]).endsWith('/status'))).toBe(true))
    expect(container.querySelector('[data-testid="auto-edit-card"]')).toBeNull()
  })
})

describe('grouping and the recipe (pure)', () => {
  it('maps a time in the original to the clean cut by taking out the kept pause cuts', async () => {
    const { cleanTime, recipeLines } = await import('@/studio/autoedit/groups')
    const cuts = [chg('auto_silence1', 'cut_segment', 'safe', 'kept', 'a', { params: { start: 1, end: 2 } }), chg('auto_silence2', 'cut_segment', 'safe', 'kept', 'b', { params: { start: 5, end: 6.5 } }),
      chg('auto_silence9', 'cut_segment', 'safe', 'reverted', 'c', { params: { start: 8, end: 9 } })] as never[]
    expect(cleanTime(0.5, cuts)).toBe(0.5)
    expect(cleanTime(4, cuts)).toBe(3)                 // one 1 s pause before it
    expect(cleanTime(10, cuts)).toBe(7.5)              // 1 s + 1.5 s; the reverted cut does not count
    expect(cleanTime(5.5, cuts)).toBeCloseTo(4)        // inside a cut: lands at the cut's start (5 s minus the 1 s before it)
    const lines = recipeLines([...cuts, chg('z', 'zoom_punch', 'risky', 'kept', 'z', { params: { at: 7, scale: 1.1, duration: 0.25 } }),
      chg('l', 'text_overlay', 'risky', 'kept', 'l', { params: { role: 'label', text: 'Step 2', start: 6.6, end: 8, anchor: 'source' } })] as never[])
    expect(lines).toEqual(['Label “Step 2”: add a text layer from 4.1 to 5.5 s.', 'Zoom punch: zoom in about 10% at 4.5 s for 0.3 s.'].map(l => l.replace('0.3 s', '0.3 s')))
  })
})
