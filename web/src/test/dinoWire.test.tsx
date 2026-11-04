import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import AttentionStrip, { type ViewerAttention } from '@/studio/AttentionStrip'
import { ClosestReelView, type Closest } from '@/studio/ClosestReel'
import { quietWindows } from '@/lib/quietWindows'

const strip: ViewerAttention = {
  label: 'attention strip (estimate)', estimate: true, note: 'Estimates from the picture and the sound of this video, not measured viewer data.',
  bin_s: 0.5, duration: 6,
  visual_change: [0.2, 0.9, 0.1, 0, 0, 0, 0, 0.4, 0.5, 0.3, 0.2, 0.1],
  audio_energy: [0.5, 0.6, 0.7, 0.1, 0, 0, 0.6, 0.7, 0.6, 0.5, 0.5, 0.6],
  audio_change: [0.1, 0.2, 0.3, 0.2, 0, 0, 0.1, 0.2, 0.3, 0.2, 0.1, 0.1],
  static_stretches: [{ start: 1.5, end: 3.5, duration: 2 }],
  loudness_dips: [{ start: 1.5, end: 3, min_db: -50 }],
  reasons: { visual_change: null, audio_energy: null, audio_change: null },
}

describe('AttentionStrip (one rhythm chart)', () => {
  it('says the rhythm in one sentence and draws ONE chart with bands only where a real dip or still stretch exists, a legend and the estimate tag', () => {
    const { container } = render(<AttentionStrip a={strip} />)
    expect(screen.getByTestId('strip-sentence').textContent).toContain('Sound dips at 1.5-3s')
    expect(screen.getByTestId('strip-sentence').textContent).toContain('picture barely changes at 1.5-3.5s')
    expect(screen.getAllByTestId('rhythm-chart')).toHaveLength(1)
    expect(screen.getAllByTestId('mark-quiet')).toHaveLength(1)
    expect(screen.getAllByTestId('mark-still')).toHaveLength(1)
    expect(screen.getByTestId('strip-legend').textContent).toContain('Quiet dip')
    expect(screen.getAllByText('estimate').length).toBeGreaterThanOrEqual(1)
    expect(screen.queryByText('Show details')).toBeNull()                      // the three bar charts are gone
    expect(container.querySelectorAll('rect').length).toBe(0)                  // no barcode
    expect(container.textContent).not.toContain('%')
  })
  it('shows no band and no legend entry for a dip that does not exist', () => {
    render(<AttentionStrip a={{ ...strip, loudness_dips: [], static_stretches: [] }} />)
    expect(screen.queryAllByTestId('mark-quiet')).toHaveLength(0)
    expect(screen.queryAllByTestId('mark-still')).toHaveLength(0)
    expect(screen.getByTestId('strip-legend').textContent).not.toContain('Quiet dip')
    expect(screen.getByTestId('strip-sentence').textContent).toContain('No long quiet or still stretch')
  })
  it('shows hover values as relative 0 to 10 numbers, not percentages', () => {
    render(<AttentionStrip a={strip} />)
    ;(window as unknown as { PointerEvent: unknown }).PointerEvent = MouseEvent   // jsdom has no PointerEvent
    const chart = screen.getByRole('img', { name: /Sound level over time/ })
    chart.getBoundingClientRect = () => ({ left: 0, top: 0, right: 600, bottom: 150, width: 600, height: 150, x: 0, y: 0, toJSON() {} }) as DOMRect
    fireEvent.pointerMove(chart, { clientX: 300 })
    const tip = screen.getByTestId('rhythm-hover').textContent || ''
    expect(tip).toMatch(/0:03 · sound \d+\/10 · picture change \d+\/10/)
    expect(tip).not.toContain('%')
  })
  it('the sentence and the window list use the same quiet windows', () => {
    render(<AttentionStrip a={{ ...strip, loudness_dips: [{ start: 19, end: 23 }, { start: 24, end: 27 }], duration: 30 }} />)
    expect(screen.getByTestId('strip-sentence').textContent).toContain('Sound dips at 19-23s and 24-27s')
    expect(screen.getAllByTestId('mark-quiet')).toHaveLength(2)
  })
  it('says why a part is missing instead of drawing it', () => {
    render(<AttentionStrip a={{ ...strip, visual_change: null, static_stretches: [], reasons: { ...strip.reasons, visual_change: 'DINOv2 is off' } }} />)
    expect(screen.getByTestId('strip-note').textContent).toContain('Picture: not available (DINOv2 is off)')
  })
})

describe('quiet windows (one source for the panel and the strip)', () => {
  it('prefers the strip\'s windows when it has audio, else the older list, else none', () => {
    expect(quietWindows(strip, [{ start: 9, end: 12 }])).toEqual({ wins: strip.loudness_dips, source: 'strip' })
    expect(quietWindows({ ...strip, audio_energy: null }, [{ start: 9, end: 12 }])).toEqual({ wins: [{ start: 9, end: 12 }], source: 'legacy' })
    expect(quietWindows(null, null).source).toBe('none')
    expect(quietWindows(strip, [{ start: 9, end: 12 }]).wins).not.toContainEqual({ start: 9, end: 12 })
  })
})

const closest: Closest = {
  connected: true, available: true, note: 'Looks similar is about pictures, not results.', own_reels: 2, compared_with: 2, small_sample: true,
  small_sample_note: 'You have fewer than 3 imported reels, so this ranking is a small sample.',
  ranked: [
    { n: 2, similarity: 0.91, band: 'looks very alike', permalink: null, postedAt: null, partial: false, hookWords: 'Stop scrolling', lines: ['Reached 2,000 accounts (40% below your usual)', 'Viewers watched on average 30% of it'] },
    { n: 1, similarity: 0.2, band: 'looks quite different', permalink: null, postedAt: null, partial: false, hookWords: null, lines: ['Reached 5,000 accounts (about your usual)'] },
  ],
}

describe('ClosestReelView', () => {
  it('names the closest reel, shows its real numbers in words, the honest note and the small-sample note', () => {
    render(<MemoryRouter><ClosestReelView c={closest} /></MemoryRouter>)
    expect(screen.getByTestId('closest-headline').textContent).toContain('Closest to your Reel 2')
    expect(screen.getAllByTestId('closest-item')).toHaveLength(2)
    expect(screen.getByTestId('closest-bar').textContent).toContain('similarity 0.91 of 1')
    expect(screen.getByTestId('closest-others').textContent).toContain('See others (1)')
    expect(screen.getByTestId('closest-thumb')).toBeTruthy()
    expect(screen.getAllByTestId('closest-lines')[0].textContent).toContain('Reached 2,000 accounts (40% below your usual)')
    expect(screen.getByTestId('closest-honest-note').textContent).toContain('pictures, not results')
    expect(screen.getByTestId('closest-small-sample').textContent).toContain('fewer than 3')
  })
  it('renders nothing at all when the comparison is not available (no "not available" text on the results page)', () => {
    const { container } = render(<MemoryRouter><ClosestReelView c={{ connected: true, available: false, reason: 'No picture embedding.', note: 'Looks similar is about pictures, not results.', ranked: [] }} /></MemoryRouter>)
    expect(container.innerHTML).toBe('')
    const again = render(<MemoryRouter><ClosestReelView c={{ connected: true, available: true, note: 'x', ranked: [] }} /></MemoryRouter>)
    expect(again.container.innerHTML).toBe('')
  })
  it('shows the "could not be compared" hint only when some reels were compared', () => {
    render(<MemoryRouter><ClosestReelView c={{ ...closest, own_reels: 4, compared_with: 2, missing_embeddings: 2 }} /></MemoryRouter>)
    expect(screen.getByTestId('closest-missing').textContent).toContain('2 of your 4 imported reels could not be compared yet')
    expect(screen.getByText('estimate')).toBeTruthy()
  })
})
