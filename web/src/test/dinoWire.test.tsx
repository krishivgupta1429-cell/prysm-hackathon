import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import AttentionStrip, { type ViewerAttention } from '@/studio/AttentionStrip'
import { ClosestReelView, type Closest } from '@/studio/ClosestReel'

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
  it('marks still stretches and quiet dips, says estimate everywhere and shows no percentages', () => {
    const { container } = render(<AttentionStrip a={strip} />)
    expect(screen.getAllByText('estimate').length).toBeGreaterThanOrEqual(4)
    expect(screen.getAllByTestId('mark-still')).toHaveLength(1)
    expect(screen.getAllByTestId('mark-quiet')).toHaveLength(1)
    expect(screen.getByTestId('strip-summary').textContent).toContain('1.5 s to 3.5 s')
    expect(container.textContent).not.toContain('%')
  })
  it('says why a part is missing instead of drawing it', () => {
    render(<AttentionStrip a={{ ...strip, visual_change: null, static_stretches: [], reasons: { ...strip.reasons, visual_change: 'DINOv2 is off' } }} />)
    expect(screen.getByTestId('row-visual').textContent).toContain('Not available: DINOv2 is off')
    expect(screen.getByTestId('strip-summary').textContent).toContain('not available')
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
  it('explains when the comparison is not available', () => {
    render(<MemoryRouter><ClosestReelView c={{ connected: true, available: false, reason: 'No picture embedding.', note: 'Looks similar is about pictures, not results.', ranked: [] }} /></MemoryRouter>)
    expect(screen.getByTestId('closest-unavailable').textContent).toContain('No picture embedding.')
  })
})
