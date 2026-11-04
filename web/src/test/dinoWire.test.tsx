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

describe('AttentionStrip', () => {
  it('says the rhythm in one sentence, draws ONE compact strip with a legend and keeps the three charts behind Show details', () => {
    const { container } = render(<AttentionStrip a={strip} />)
    expect(screen.getByTestId('strip-sentence').textContent).toContain('Sound dips at 1.5-3s')
    expect(screen.getByTestId('strip-sentence').textContent).toContain('picture barely changes at 1.5-3.5s')
    expect(screen.getByTestId('strip-compact')).toBeTruthy()
    expect(screen.getByTestId('strip-legend').textContent).toContain('Quiet dip')
    expect(screen.queryByTestId('row-visual')).toBeNull()
    expect(screen.queryByTestId('strip-details')).toBeNull()
    expect(screen.getAllByText('estimate').length).toBeGreaterThanOrEqual(1)
    expect(container.textContent).not.toContain('%')
    fireEvent.click(screen.getByText('Show details'))
    expect(screen.getByTestId('row-visual')).toBeTruthy()
    expect(screen.getByTestId('row-energy')).toBeTruthy()
    expect(screen.getByTestId('row-change')).toBeTruthy()
    expect(screen.getByTestId('strip-summary').textContent).toContain('1.5 s to 3.5 s')
    expect(screen.getAllByText('estimate').length).toBeGreaterThanOrEqual(4)
    fireEvent.click(screen.getByText('Hide details'))
    expect(screen.queryByTestId('strip-details')).toBeNull()
  })
  it('the sentence and the detail list use the same quiet windows (no "none found" next to a flagged dip)', () => {
    render(<AttentionStrip a={{ ...strip, loudness_dips: [{ start: 19, end: 23 }, { start: 24, end: 27 }], duration: 30 }} />)
    expect(screen.getByTestId('strip-sentence').textContent).toContain('Sound dips at 19-23s and 24-27s')
    fireEvent.click(screen.getByText('Show details'))
    expect(screen.getByTestId('strip-summary').textContent).toContain('19 s to 23 s, 24 s to 27 s')
    expect(screen.getByTestId('strip-summary').textContent).not.toContain('Quiet dips: none found')
  })
  it('says why a part is missing instead of drawing it', () => {
    render(<AttentionStrip a={{ ...strip, visual_change: null, static_stretches: [], reasons: { ...strip.reasons, visual_change: 'DINOv2 is off' } }} />)
    fireEvent.click(screen.getByText('Show details'))
    expect(screen.getByTestId('row-visual').textContent).toContain('Not available: DINOv2 is off')
    expect(screen.getByTestId('strip-summary').textContent).toContain('not available')
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
