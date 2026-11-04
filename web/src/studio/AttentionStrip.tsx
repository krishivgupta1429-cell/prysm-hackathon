// Built during the Meta Global AI Developer Hackathon (new work).
// "This video's rhythm (our estimate)" inside the Viewer behaviour card: ONE plain sentence, then ONE chart: a smooth soft-filled
// sound-level line over time, a thin picture-change line, shaded bands only where a real quiet dip or still stretch exists, hover values
// and a short legend. The three bar charts are gone. Every part says "estimate"; the values are relative to this video (0 to 10), never
// percentages or predictions of what viewers will do. The quiet windows are the ones the server computed once (lib/quietWindows).
import { useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { rhythmSentence } from '@/lib/quietWindows'
import { INK, INK2, tag } from './autoedit/ui'

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

const W = 600, H = 150, PAD = 10
const secs = (s: number) => `${Number.isInteger(s) ? s : s.toFixed(1)} s`
const span = (s: Span) => `${secs(s.start)} to ${secs(s.end)}`
const mm = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`
const STILL = 'rgba(196,136,30,0.20)'
const QUIET = 'rgba(31,31,31,0.10)'

/** A smooth path through the points (Catmull-Rom turned into cubic Beziers), kept inside the plot. */
function smooth(pts: [number, number][]): string {
  if (pts.length < 2) return ''
  const clampY = (y: number) => Math.max(PAD, Math.min(H - PAD, y))
  let d = `M ${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2
    const c1: [number, number] = [p1[0] + (p2[0] - p0[0]) / 6, clampY(p1[1] + (p2[1] - p0[1]) / 6)]
    const c2: [number, number] = [p2[0] - (p3[0] - p1[0]) / 6, clampY(p2[1] - (p3[1] - p1[1]) / 6)]
    d += ` C ${c1[0].toFixed(1)} ${c1[1].toFixed(1)}, ${c2[0].toFixed(1)} ${c2[1].toFixed(1)}, ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`
  }
  return d
}

function Swatch({ style }: { style: CSSProperties }) { return <span style={{ display: 'inline-block', verticalAlign: 'middle', marginRight: 6, ...style }} /> }

export default function AttentionStrip({ a }: { a: ViewerAttention }) {
  const box = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<number | null>(null)
  const dur = a.duration || 1
  const sound = a.audio_energy, picture = a.visual_change
  const x = (i: number, n: number) => (n <= 1 ? 0 : (i / (n - 1)) * W)
  const y = (v: number) => H - PAD - Math.max(0, Math.min(1, v)) * (H - 2 * PAD)
  const line = (vals: (number | null)[] | null) => vals ? smooth(vals.map((v, i) => [x(i, vals.length), y(v ?? 0)] as [number, number])) : ''
  const soundPath = line(sound)
  const area = soundPath ? `${soundPath} L ${W} ${H - PAD} L 0 ${H - PAD} Z` : ''
  const at = (t: number | null, vals: (number | null)[] | null) => (t == null || !vals ? null : vals[Math.min(vals.length - 1, Math.max(0, Math.round((t / dur) * (vals.length - 1))))])
  const ten = (v: number | null | undefined) => (v == null ? '–' : `${Math.round(v * 10)}/10`)
  const quiet = sound ? a.loudness_dips || [] : []
  const still = picture ? a.static_stretches || [] : []
  const nothing = !sound && !picture
  const move = (e: React.PointerEvent) => {
    const r = box.current?.getBoundingClientRect()
    if (!r || !r.width) return
    setHover(Math.max(0, Math.min(dur, ((e.clientX - r.left) / r.width) * dur)))
  }
  const band = (s: Span, kind: 'quiet' | 'still') => (
    <div key={`${kind}${s.start}`} data-testid={`mark-${kind}`} title={`${kind === 'quiet' ? 'Much quieter than the rest' : 'Picture barely changes'}: ${span(s)}`}
      style={{ position: 'absolute', top: 0, bottom: 0, left: `${(s.start / dur) * 100}%`, width: `${Math.max(0.8, ((s.end - s.start) / dur) * 100)}%`, background: kind === 'quiet' ? QUIET : STILL,
        borderLeft: `1px ${kind === 'quiet' ? 'dashed' : 'solid'} ${kind === 'quiet' ? INK : '#c4881e'}`, borderRight: `1px ${kind === 'quiet' ? 'dashed' : 'solid'} ${kind === 'quiet' ? INK : '#c4881e'}`, boxSizing: 'border-box', pointerEvents: 'none' }} />
  )
  return (
    <div style={{ borderTop: '1px solid hsl(var(--border))', marginTop: 18, paddingTop: 16 }} data-testid="attention-strip">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 14, fontWeight: 700 }}>This video&rsquo;s rhythm <span style={{ fontWeight: 500, color: INK2 }}>(our estimate)</span></span>
        <span style={tag}>estimate</span>
      </div>
      <p style={{ fontSize: 15, lineHeight: 1.5, margin: '8px 0 12px', color: INK }} data-testid="strip-sentence">{rhythmSentence(a)}</p>
      {!nothing && (
        <div data-testid="rhythm-chart">
          <div ref={box} onPointerMove={move} onPointerLeave={() => setHover(null)} style={{ position: 'relative', height: 150, touchAction: 'pan-y', cursor: 'crosshair', borderRadius: 12, background: 'rgba(31,31,31,0.025)', overflow: 'hidden' }}
            role="img" aria-label={`Sound level over time with a thin picture-change line, estimate. ${rhythmSentence(a)}`}>
            {quiet.map(s => band(s, 'quiet'))}
            {still.map(s => band(s, 'still'))}
            <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} aria-hidden="true">
              <defs><linearGradient id="soundfill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={INK} stopOpacity="0.28" /><stop offset="1" stopColor={INK} stopOpacity="0.03" /></linearGradient></defs>
              {area && <path d={area} fill="url(#soundfill)" />}
              {soundPath && <path d={soundPath} fill="none" stroke={INK} strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />}
              {picture && <path d={line(picture)} fill="none" stroke="#8a6a2a" strokeWidth="1" vectorEffect="non-scaling-stroke" strokeDasharray="3 3" />}
            </svg>
            {hover != null && (
              <>
                <div style={{ position: 'absolute', top: 0, bottom: 0, left: `${(hover / dur) * 100}%`, width: 1, background: INK, opacity: 0.55, pointerEvents: 'none' }} />
                <div data-testid="rhythm-hover" style={{ position: 'absolute', top: 6, left: hover / dur > 0.6 ? undefined : `calc(${(hover / dur) * 100}% + 8px)`, right: hover / dur > 0.6 ? `calc(${(1 - hover / dur) * 100}% + 8px)` : undefined,
                  background: INK, color: '#fff', borderRadius: 8, padding: '5px 9px', fontSize: 12, lineHeight: 1.4, pointerEvents: 'none', whiteSpace: 'nowrap' }}>
                  {mm(hover)} · sound {ten(at(hover, sound))}{picture ? ` · picture change ${ten(at(hover, picture))}` : ''}
                </div>
              </>
            )}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: INK2, marginTop: 4 }}><span>0:00</span><span>{mm(dur / 2)}</span><span>{mm(dur)}</span></div>
          <div style={{ display: 'flex', gap: '6px 16px', flexWrap: 'wrap', fontSize: 12, color: INK2, marginTop: 8 }} data-testid="strip-legend">
            {sound && <span><Swatch style={{ width: 16, height: 3, background: INK, borderRadius: 2 }} />Sound level</span>}
            {picture && <span><Swatch style={{ width: 16, height: 0, borderTop: '1px dashed #8a6a2a' }} />Picture change</span>}
            {quiet.length > 0 && <span><Swatch style={{ width: 12, height: 12, background: QUIET, border: `1px dashed ${INK}`, boxSizing: 'border-box' }} />Quiet dip</span>}
            {still.length > 0 && <span><Swatch style={{ width: 12, height: 12, background: STILL, border: '1px solid #c4881e', boxSizing: 'border-box' }} />Picture barely changes</span>}
          </div>
        </div>
      )}
      <p className="p-note" style={{ margin: '8px 0 0' }} data-testid="strip-note">
        {a.note || 'Estimates from the picture and the sound of this video, not measured viewer data.'} Values run 0 to 10 relative to this video.
        {!sound && a.reasons.audio_energy ? ` Sound: not available (${a.reasons.audio_energy}).` : ''}{!picture && a.reasons.visual_change ? ` Picture: not available (${a.reasons.visual_change}).` : ''}
      </p>
    </div>
  )
}
