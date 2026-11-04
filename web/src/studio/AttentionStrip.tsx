// Built during the Meta Global AI Developer Hackathon (new work).
// "This video's rhythm (our estimate)" inside the Viewer behaviour card: ONE plain sentence ("Sound dips at 19-23s ..."), then ONE compact
// strip (sound level per half second, still stretches shaded, quiet dips outlined) with a small legend. The three separate bar charts
// (visual change, audio energy, audio change) sit behind "Show details". Every part says "estimate"; no percentages, no predictions of
// what viewers will do. The quiet windows are the same ones the Audio Energy panel uses (lib/quietWindows). A part the server could not
// compute says why.
import { useState } from 'react'
import type { CSSProperties } from 'react'
import { rhythmSentence } from '@/lib/quietWindows'

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
const mm = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`
const STILL = 'rgba(10,10,10,0.13)'

function Bars({ values, h, dur, spans, kind, label }: { values: (number | null)[]; h: number; dur: number; spans: Span[]; kind: 'still' | 'quiet'; label: string }) {
  const n = values.length || 1
  return (
    <div style={{ position: 'relative', height: h, background: '#fafafa', borderRadius: 8, overflow: 'hidden' }} role="img"
      aria-label={`${label}, estimate${spans.length ? `; ${kind === 'still' ? 'still' : 'quiet'} stretches at ${spans.map(span).join(', ')}` : ''}`}>
      {spans.map((s, i) => (
        <div key={i} data-testid={`mark-${kind}`} title={`${kind === 'still' ? 'Picture barely changes' : 'Much quieter than the rest'}: ${span(s)}`}
          style={{
            position: 'absolute', top: 0, bottom: 0, left: `${(s.start / dur) * 100}%`, width: `${Math.max(0.6, ((s.end - s.start) / dur) * 100)}%`,
            background: kind === 'still' ? STILL : 'rgba(10,10,10,0.05)',
            border: kind === 'quiet' ? '1.5px dashed #0a0a0a' : 'none', boxSizing: 'border-box',
          }} />
      ))}
      <svg viewBox={`0 0 ${n} 40`} preserveAspectRatio="none" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} aria-hidden="true">
        {values.map((v, i) => (v == null ? null : <rect key={i} x={i + 0.1} y={40 - v * 38 - 1} width={0.8} height={v * 38 + 1} fill="#0a0a0a" />))}
      </svg>
    </div>
  )
}

function Row({ id, label, values, dur, spans, kind, reason }: {
  id: string; label: string; values: (number | null)[] | null; dur: number; spans: Span[]; kind: 'still' | 'quiet'; reason: string | null
}) {
  return (
    <div style={{ marginBottom: 12 }} data-testid={`row-${id}`}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4, gap: 8 }}>
        <span style={{ fontSize: 12, color: '#525252' }}>{label}</span>
        <span style={tag}>estimate</span>
      </div>
      {values ? <Bars values={values} h={48} dur={dur} spans={spans} kind={kind} label={label} />
        : <div style={{ ...muted, margin: 0, padding: 12, background: '#fafafa', borderRadius: 8 }}>Not available: {reason || 'no reason given'}</div>}
    </div>
  )
}

export default function AttentionStrip({ a }: { a: ViewerAttention }) {
  const [open, setOpen] = useState(false)
  const dur = a.duration || 1
  // the compact strip draws the sound level; a video with no usable audio draws the picture change instead
  const main = a.audio_energy ? { v: a.audio_energy, k: 'quiet' as const, spans: a.loudness_dips, label: 'Sound level' }
    : a.visual_change ? { v: a.visual_change, k: 'still' as const, spans: a.static_stretches, label: 'Picture change' } : null
  return (
    <div style={{ borderTop: '1px solid #eee', marginTop: 16, paddingTop: 14 }} data-testid="attention-strip">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 700 }}>This video&rsquo;s rhythm <span style={{ fontWeight: 500, color: '#737373' }}>(our estimate)</span></span>
        <span style={tag}>estimate</span>
      </div>
      <p style={{ fontSize: 14, lineHeight: 1.45, margin: '8px 0 12px', color: '#0a0a0a' }} data-testid="strip-sentence">{rhythmSentence(a)}</p>
      {main ? (
        <div data-testid="strip-compact">
          <Bars values={main.v} h={40} dur={dur} spans={main.spans} kind={main.k} label={main.label} />
          {main.k === 'quiet' && a.visual_change && a.static_stretches.length > 0 && (
            <div style={{ position: 'relative', height: 6, marginTop: 4, background: '#f3f3f3', borderRadius: 4 }} aria-label="Stretches where the picture barely changes, estimate" role="img">
              {a.static_stretches.map((s, i) => (
                <div key={i} data-testid="mark-still" title={`Picture barely changes: ${span(s)}`}
                  style={{ position: 'absolute', top: 0, bottom: 0, left: `${(s.start / dur) * 100}%`, width: `${Math.max(0.6, ((s.end - s.start) / dur) * 100)}%`, background: '#737373', borderRadius: 3 }} />
              ))}
            </div>
          )}
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#a3a3a3', marginTop: 4 }}><span>0:00</span><span>{mm(dur / 2)}</span><span>{mm(dur)}</span></div>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', fontSize: 11, color: '#737373', marginTop: 8 }} data-testid="strip-legend">
            <span><span style={{ display: 'inline-block', width: 3, height: 10, background: '#0a0a0a', verticalAlign: 'middle', marginRight: 6 }} />Sound level</span>
            {main.k === 'quiet' && <span><span style={{ display: 'inline-block', width: 12, height: 10, border: '1.5px dashed #0a0a0a', boxSizing: 'border-box', verticalAlign: 'middle', marginRight: 6 }} />Quiet dip</span>}
            {main.k === 'quiet' && a.visual_change && a.static_stretches.length > 0 && <span><span style={{ display: 'inline-block', width: 12, height: 6, background: '#737373', borderRadius: 3, verticalAlign: 'middle', marginRight: 6 }} />Picture barely changes</span>}
          </div>
        </div>
      ) : null}
      <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
        style={{ all: 'unset', cursor: 'pointer', fontSize: 12, fontWeight: 600, color: '#525252', marginTop: 12, textDecoration: 'underline', textUnderlineOffset: 3 }}>
        {open ? 'Hide details' : 'Show details'}
      </button>
      {open && (
        <div style={{ marginTop: 12 }} data-testid="strip-details">
          <p style={{ fontSize: 12, color: '#737373', margin: '0 0 12px', lineHeight: 1.4 }}>
            How much the picture changes, how loud the sound is and how much the sound changes, one bar per half second. {a.note}
          </p>
          <Row id="visual" label="Visual change" values={a.visual_change} dur={dur} spans={a.static_stretches} kind="still" reason={a.reasons.visual_change} />
          <Row id="energy" label="Audio energy (loudness)" values={a.audio_energy} dur={dur} spans={a.loudness_dips} kind="quiet" reason={a.reasons.audio_energy} />
          <Row id="change" label="Audio change" values={a.audio_change} dur={dur} spans={[]} kind="quiet" reason={a.reasons.audio_change} />
          <div style={{ fontSize: 12, lineHeight: 1.6, marginTop: 6 }} data-testid="strip-summary">
            <div><strong>Still stretches:</strong> {a.visual_change ? (a.static_stretches.length ? a.static_stretches.map(span).join(', ') : 'none found') : 'not available'}</div>
            <div><strong>Quiet dips:</strong> {a.audio_energy ? (a.loudness_dips.length ? a.loudness_dips.map(span).join(', ') : 'none found') : 'not available'}</div>
          </div>
        </div>
      )}
    </div>
  )
}
