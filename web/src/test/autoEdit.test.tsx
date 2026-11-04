// Built during the Meta Global AI Developer Hackathon (new work).
import { render, screen, waitFor, within } from '@testing-library/react'
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
    if (url.includes('/download/')) return new Response(new Blob(['x']), { status: 200 })
    return new Response('{}', { status: 200 })
  }),
}))

import Edit from '@/pages/Edit'
import AutoEditCard from '@/studio/AutoEditCard'
import { authFetch } from '@/lib/auth'

const chg = (id: string, type: string, risk: string, verdict: string, label: string, extra: object = {}) =>
  ({ id, type, group: 'pacing', label, reason: `${label} reason`, target_parameter: '', enabled: verdict !== 'reverted', verdict, verdict_reasons: [], user_override: false, risk, ...extra })

const done = {
  eligible: true, reason: '', state: {
    status: 'done', phase: 'done', preset: 'clean', presets: ['clean', 'energetic', 'minimal'], running: false, trace: [{ t: '0', kind: 'info', msg: 'raw log line' }],
    changes: [chg('auto_silence1', 'cut_segment', 'safe', 'kept', 'Tighten a pause'), chg('auto_silence2', 'cut_segment', 'safe', 'kept', 'Tighten another pause'), chg('mz1', 'zoom_punch', 'risky', 'reverted', 'Zoom punch')],
    groups: {}, manual_tips: Array.from({ length: 8 }, (_, i) => ({ parameter: 'Hook', tip: `tip number ${i + 1}` })), revisions: [], revisions_left: 5,
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

describe('Auto-edit studio page', () => {
  it('shows the named sections, groups the changes, and never lets the re-score contradict the report', async () => {
    statusBody = done
    mount()
    await screen.findByText('Safe fixes')
    expect(screen.getByText('Taste calls')).toBeTruthy()
    expect(within(screen.getByTestId('safe-fixes')).getAllByTestId('change-row')).toHaveLength(2)
    expect(screen.getAllByTestId('verdict-chip').map(c => c.textContent)).toEqual(['KEPT', 'KEPT', 'REVERTED'])
    for (const t of ['Changes', 'Style', 'Ask for changes', 'Verified', 'Do it yourself', 'Export for Instagram Edits']) expect(screen.getAllByText(t).length).toBeGreaterThan(0)
    // measured table: renamed row plus the pauses-removed row
    const table = screen.getByTestId('measured-table')
    expect(within(table).getByText('Hard cuts (shot changes)')).toBeTruthy()
    expect(within(table).getByText('Pauses removed').closest('tr')!.textContent).toContain('2')
    // the re-score starts from the REPORT's scores (64, 52), not the AI run's own 48 / 42
    const rescore = screen.getByTestId('rescore')
    expect(rescore.textContent).toContain('AI-estimated, informational')
    expect(within(rescore).getByText('Hook & Opening').closest('tr')!.textContent).toBe('Hook & Opening6466+2')
    expect(within(rescore).getByText('Retention & Pacing').closest('tr')!.textContent).toBe('Retention & Pacing5257+5')
    expect(rescore.textContent).not.toMatch(/\b48\b|\b42\b/)
    // 8 tips: 5 shown, "3 more" folded
    expect(screen.getByText('3 more')).toBeTruthy()
    // the raw log is collapsed in an Activity log; export block claims no integration
    expect(screen.getByTestId('activity-log').hasAttribute('open')).toBe(false)
    expect(screen.getByTestId('export').textContent).toContain('not connected to Instagram Edits')
    expect(screen.getByTestId('export-steps').querySelectorAll('li')).toHaveLength(3)
    // everything went through authFetch
    expect(vi.mocked(authFetch).mock.calls.length).toBeGreaterThan(2)
  })

  it('hides the re-score when the stored report is for another analysis', async () => {
    statusBody = done
    localStorage.setItem('prysm_result', JSON.stringify({ analysis_id: 'other', parameters: [{ name: 'Hook & Opening', score: 64 }] }))
    mount()
    await screen.findByText('Safe fixes')
    expect(screen.queryByTestId('rescore')).toBeNull()
    expect(screen.getByTestId('back-link').textContent).toContain('dashboard')
  })

  it('names the stage that is running in a stepper', async () => {
    statusBody = { eligible: true, reason: '', state: { ...done.state, running: true, status: 'running', phase: 'planning', final: null } }
    mount()
    const stepper = await screen.findByTestId('stepper')
    const now = Array.from(stepper.querySelectorAll('li')).find(li => li.getAttribute('data-state') === 'now')!
    expect(now.textContent).toContain('Planning the edit')
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
  it('shows the number of kept changes and opens the studio when an edit exists', async () => {
    statusBody = done
    card()
    const link = await screen.findByRole('link', { name: 'Open Auto-edit studio' })
    expect(link.getAttribute('href')).toBe('/edit/abc')
    await waitFor(() => expect(screen.getByTestId('auto-edit-card-line').textContent).toContain('2 changes kept'))
  })
  it('renders nothing when the analysis cannot be edited and has no edit', async () => {
    statusBody = { eligible: false, reason: 'Links cannot be edited.', state: null }
    const { container } = card()
    await waitFor(() => expect(vi.mocked(authFetch).mock.calls.some(c => String(c[0]).endsWith('/status'))).toBe(true))
    expect(container.querySelector('[data-testid="auto-edit-card"]')).toBeNull()
  })
})
