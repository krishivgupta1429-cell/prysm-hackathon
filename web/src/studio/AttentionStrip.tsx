// Built during the Meta Global AI Developer Hackathon (new work).
// The "attention strip" inside the Predicted Viewer Behaviour card: how much the picture changes, how loud the sound is and
// how much the sound changes, one bar per half second. Every row says "estimate". No percentages and no predictions of what
// viewers will do: it only shows what the video itself does. Still stretches (picture barely changes) are shaded on the picture
// row; quiet dips (much quieter than the rest) are outlined on the loudness row. A part the server could not compute says why.
import type { CSSProperties } from 'react'

export type Span = { start: number; end: number; duration?: number }
export type ViewerAttention = {
  label: string
  estimate: boolean
  note: string
  bin_s: number
  duration: number
  visual_change: (number | null)[] | null
  audio_energy: (number | null)[] | null
  audio_change: (number | null)[] | null
  static_stretches: Span[]
  loudness_dips: (Span & { min_db?: number })[]
  reasons: { visual_change: string | null; audio_energy: string | null; audio_change: string | null }
}

const tag: CSSProperties = { fontSize: 9, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', padding: '2px 8px', border: '1px solid #d4d4d4', borderRadius: 999, color: '#737373', whiteSpace: 'nowrap' }
const muted: CSSProperties = { fontSize: 11, color: '#a3a3a3', margin: '6px 0 0', lineHeight: 1.4 }
const secs = (s: number) => `${Number.isInteger(s) ? s : s.toFixed(1)} s`
const span = (s: Span) => `${secs(s.start)} to ${secs(s.end)}`

function Row({ id, label, values, dur, spans, kind, reason }: {
  id: string; label: string; values: (number | null)[] | null; dur: number; spans: Span[]; kind: 'still' | 'quiet'; reason: string | null
}) {
  const n = values?.length || 1
  return (
    <div style={{ marginBottom: 12 }} data-testid={`row-${id}`}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4, gap: 8 }}>
        <span style={{ fontSize: 12, color: '#525252' }}>{label}</span>
        <span style={tag}>estimate</span>
      </div>
      {values ? (
        <div style={{ position: 'relative', height: 48, background: '#fafafa', borderRadius: 8, overflow: 'hidden' }} role="img"
          aria-label={`${label}, estimate${spans.length ? `; ${kind === 'still' ? 'still' : 'quiet'} stretches at ${spans.map(span).join(', ')}` : ''}`}>
          {spans.map((s, i) => (
            <div key={i} data-testid={`mark-${kind}`} title={`${kind === 'still' ? 'Picture barely changes' : 'Much quieter than the rest'}: ${span(s)}`}
              style={{
                position: 'absolute', top: 0, bottom: 0, left: `${(s.start / dur) * 100}%`, width: `${Math.max(0.6, ((s.end - s.start) / dur) * 100)}%`,
                background: kind === 'still' ? 'rgba(10,10,10,0.13)' : 'rgba(10,10,10,0.05)',
                border: kind === 'quiet' ? '1.5px dashed #0a0a0a' : 'none', boxSizing: 'border-box',
              }} />
          ))}
          <svg viewBox={`0 0 ${n} 40`} preserveAspectRatio="none" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} aria-hidden="true">
            {values.map((v, i) => (v == null ? null : <rect key={i} x={i + 0.1} y={40 - v * 38 - 1} width={0.8} height={v * 38 + 1} fill="#0a0a0a" />))}
          </svg>
        </div>
      ) : (
        <div style={{ ...muted, margin: 0, padding: 12, background: '#fafafa', borderRadius: 8 }}>Not available: {reason || 'no reason given'}</div>
      )}
    </div>
  )
}

export default function AttentionStrip({ a }: { a: ViewerAttention }) {
  const dur = a.duration || 1
  const mm = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`
  return (
    <div style={{ borderTop: '1px solid #eee', marginTop: 16, paddingTop: 12 }} data-testid="attention-strip">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 700 }}>What this video does, moment by moment</span>
        <span style={tag}>estimate</span>
      </div>
      <p style={{ fontSize: 12, color: '#737373', margin: '4px 0 12px', lineHeight: 1.4 }}>
        How much the picture changes, how loud the sound is and how much the sound changes, one bar per half second. {a.note}
      </p>
      <Row id="visual" label="Visual change" values={a.visual_change} dur={dur} spans={a.static_stretches} kind="still" reason={a.reasons.visual_change} />
      <Row id="energy" label="Audio energy (loudness)" values={a.audio_energy} dur={dur} spans={a.loudness_dips} kind="quiet" reason={a.reasons.audio_energy} />
      <Row id="change" label="Audio change" values={a.audio_change} dur={dur} spans={[]} kind="quiet" reason={a.reasons.audio_change} />
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#a3a3a3' }}><span>0:00</span><span>{mm(dur / 2)}</span><span>{mm(dur)}</span></div>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 11, color: '#737373', marginTop: 10 }}>
        <span><span style={{ display: 'inline-block', width: 12, height: 10, background: 'rgba(10,10,10,0.13)', verticalAlign: 'middle', marginRight: 6 }} />Still stretch: the picture barely changes</span>
        <span><span style={{ display: 'inline-block', width: 12, height: 10, border: '1.5px dashed #0a0a0a', boxSizing: 'border-box', verticalAlign: 'middle', marginRight: 6 }} />Quiet dip: much quieter than the rest</span>
      </div>
      <div style={{ fontSize: 12, lineHeight: 1.6, marginTop: 10 }} data-testid="strip-summary">
        <div><strong>Still stretches:</strong> {a.visual_change ? (a.static_stretches.length ? a.static_stretches.map(span).join(', ') : 'none found') : 'not available'}</div>
        <div><strong>Quiet dips:</strong> {a.audio_energy ? (a.loudness_dips.length ? a.loudness_dips.map(span).join(', ') : 'none found') : 'not available'}</div>
      </div>
    </div>
  )
}
