// Built during the Meta Global AI Developer Hackathon (new work).
// Results page: "How this compares to your last N reels" (this video's fingerprint against the creator's top and bottom reels)
// and the small "Personalised from your reels" tag. Black-and-white, inline styles. Patterns in a small sample, never proof.
import type { CSSProperties } from 'react'
import { Link } from 'react-router-dom'

export type CreatorDnaBlock = {
  personalised: boolean
  sampleSize: number
  isFixture?: boolean
  newFingerprint?: { hook_type: string; format: string; pacing: string; audio_type: string; on_screen_text: boolean; topic: string } | null
  comparison: {
    field: string; label: string; value: string | boolean
    top_match: number; top_total: number; top_reels: number[]
    bottom_match: number; bottom_total: number; bottom_reels: number[]
    verdict: 'top' | 'bottom' | 'neutral'
  }[]
  topReels: number[]
  bottomReels: number[]
}

const tag: CSSProperties = { fontSize: 9, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', padding: '2px 8px', border: '1px solid #d4d4d4', borderRadius: 999, color: '#737373', whiteSpace: 'nowrap', display: 'inline-block' }
const VERDICT = { top: 'Like your stronger reels', bottom: 'Like your weaker reels', neutral: 'No clear difference' }

export function PersonalisedTag({ fixture }: { fixture?: boolean }) {
  return <span style={tag} data-testid="personalised-tag">{fixture ? 'Personalised from test reels' : 'Personalised from your reels'}</span>
}

const val = (v: string | boolean) => (typeof v === 'boolean' ? (v ? 'yes' : 'no') : v)

export function CreatorDnaCard({ block }: { block: CreatorDnaBlock }) {
  return (
    <div style={{ border: '1px solid #e5e5e5', borderRadius: 16, padding: 20, background: '#fff', color: '#0a0a0a', marginBottom: 20 }} data-testid="creator-dna-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>How this compares to your last {block.sampleSize} reels</h2>
        <PersonalisedTag fixture={block.isFixture} />
      </div>
      <p style={{ fontSize: 12, color: '#737373', margin: '4px 0 14px', lineHeight: 1.4 }}>
        This video's fingerprint against your {block.topReels.length} strongest and {block.bottomReels.length} weakest reels (by your own Instagram numbers).
        A pattern in a small sample, not proof.{block.isFixture ? ' TEST DATA from the development fixture.' : ''}
      </p>
      {block.comparison.length === 0 ? (
        <p style={{ fontSize: 14, color: '#404040' }}>Not enough fingerprint data to compare this video.</p>
      ) : block.comparison.map(c => (
        <div key={c.field} style={{ borderTop: '1px solid #eee', padding: '10px 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 14 }}><b>{c.label}:</b> {val(c.value)}</span>
            <span style={{ ...tag, color: c.verdict === 'neutral' ? '#737373' : '#0a0a0a', borderColor: c.verdict === 'neutral' ? '#d4d4d4' : '#0a0a0a' }}>{VERDICT[c.verdict]}</span>
          </div>
          <p style={{ fontSize: 12, color: '#737373', margin: '3px 0 0' }}>
            In {c.top_match} of {c.top_total} strongest reels{c.top_reels.length ? ` (Reel${c.top_reels.length > 1 ? 's' : ''} ${c.top_reels.join(', ')})` : ''}
            {' · '}in {c.bottom_match} of {c.bottom_total} weakest{c.bottom_reels.length ? ` (Reel${c.bottom_reels.length > 1 ? 's' : ''} ${c.bottom_reels.join(', ')})` : ''}
          </p>
        </div>
      ))}
      <p style={{ fontSize: 12, margin: '12px 0 0' }}><Link to="/creator-dna" style={{ color: '#0a0a0a' }}>See your Creator DNA</Link></p>
    </div>
  )
}
