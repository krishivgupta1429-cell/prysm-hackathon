// Built during the Meta Global AI Developer Hackathon (new work).
// "Viewer behaviour". Two plain-English sections: "Your account so far (measured)" (usually watched, skipped early, typical reach, from the
// creator's own imported reels, with "based on your last N reels" and "small sample" under 5) and "This video's rhythm (our estimate)"
// (one sentence + one compact strip, details behind a toggle). Without Instagram data there are no percentage bars: a line links to the connect flow.
// The zero-shot Qwen hook/watch percentages (near-constant, about 70% for every video) are hidden unless VITE_SHOW_QWEN_CARD=1, and
// then they still say "Rough estimate from frame content, not measured watch data". The separate "Visual quality signal" from Q-Align stays.
// Source "evqa" (a model trained on watch data) keeps its bars. Black-and-white, inline styles. The subtitle says honestly where the numbers
// come from; when the prediction is unavailable no bars are drawn. Q-Align is a picture-quality score from 1 to 5,
// never a percentage and never a watch prediction.
import { useEffect, useState, type CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { apiUrl } from '@/lib/api'
import { authFetch, getToken } from '@/lib/auth'
import { SHOW_QWEN_CARD } from '@/lib/config'
import AttentionStrip, { type ViewerAttention } from './AttentionStrip'
import { useCountUp } from '@/lib/motion'
import { card, INK2 } from './autoedit/ui'

export type QAlign = { quality: number; aesthetic: number; band: string; scale?: number[]; source?: string }
export type Engagement = {
  available?: boolean
  reason?: string | null
  nawp: number | null
  ecr: number | null
  hook_risk: 'low' | 'medium' | 'high' | null
  retention_risk: 'low' | 'medium' | 'high' | null
  source: string
  interpretation?: string
  qalign?: QAlign | null
}

const SUBTITLE: Record<string, string> = {
  evqa: 'Estimate from a model trained on short-form video watch data (Snapchat-based), not measured Instagram data',
  qwen_zero_shot: 'Rough estimate from frame content, not measured watch data',
}
const SOURCE_LABEL: Record<string, string> = { evqa: 'LMM-EVQA', qwen_zero_shot: 'Qwen2.5-VL, zero-shot' }
const HOOK: Record<string, string> = { low: 'strong hook', medium: 'average hook', high: 'weak hook' }
const RETAIN: Record<string, string> = { low: 'high retention', medium: 'average retention', high: 'low retention' }

const tag: CSSProperties = { fontSize: 12, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', padding: '2px 8px', border: '1px solid hsl(var(--border))', borderRadius: 999, color: INK2, whiteSpace: 'nowrap' }
const sub: CSSProperties = { fontSize: 12, color: INK2, margin: '4px 0 16px', lineHeight: 1.5 }
const track: CSSProperties = { height: 8, background: 'rgba(31,31,31,0.08)', borderRadius: 999, overflow: 'hidden' }

function Bar({ label, value, caption, note }: { label: string; value: number; caption: string; note: string }) {
  const pct = Math.max(0, Math.min(100, value))
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
        <span style={{ fontSize: 13, color: '#403d37' }}>{label}</span>
        <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 15, fontWeight: 800 }}>{Math.round(pct)}%</span>
          <span style={tag}>{caption}</span>
        </span>
      </div>
      <div style={track}><div style={{ width: `${pct}%`, height: '100%', background: '#1f1f1f', borderRadius: 999 }} /></div>
      <p style={{ fontSize: 12, color: '#55524b', margin: '4px 0 0' }}>{note}</p>
    </div>
  )
}

function QualityPanel({ q }: { q: QAlign }) {
  const pos = (v: number) => `${Math.max(0, Math.min(100, ((v - 1) / 4) * 100))}%`
  return (
    <div className="p-card" style={{ padding: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <h3 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Visual quality signal</h3>
        <span style={tag}>Q-Align</span>
      </div>
      <p style={sub}>How good the picture looks, scored 1 to 5. Not a watch prediction.</p>
      <div style={{ fontSize: 28, fontWeight: 800, lineHeight: 1 }}>{q.band}</div>
      <div style={{ fontSize: 13, color: '#403d37', margin: '6px 0 14px' }}>{q.quality.toFixed(1)} / 5 video quality</div>
      <div style={{ position: 'relative', height: 4, background: 'rgba(31,31,31,0.12)', borderRadius: 999 }}>
        <div style={{ position: 'absolute', top: -4, left: pos(q.quality), width: 12, height: 12, marginLeft: -6, background: '#1f1f1f', borderRadius: 999 }} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#55524b', marginTop: 8 }}><span>Bad</span><span>Poor</span><span>Fair</span><span>Good</span><span>Excellent</span></div>
      <div style={{ fontSize: 12, color: '#55524b', marginTop: 12 }}>Aesthetics {q.aesthetic.toFixed(1)} / 5</div>
    </div>
  )
}

export type Measured = {
  connected: boolean; isFixture?: boolean; reels?: number; username?: string
  avgWatchThrough?: number | null; watchThroughReels?: number; partialReels?: number; avgSkipRate?: number | null; medianReach?: number | null
}

const SMALL_SAMPLE_BELOW = 5

function Tile({ label, value, unit, testid }: { label: string; value: number | null | undefined; unit: string; testid: string }) {
  const v = useCountUp(value ?? null)
  return (
    <div style={{ ...card, padding: '12px 12px 10px', minWidth: 0 }} data-testid={testid}>
      <div style={{ fontSize: 12, color: INK2, lineHeight: 1.3 }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 800, lineHeight: 1.15, marginTop: 4 }}>{v == null ? 'n/a' : Math.round(v).toLocaleString()}</div>
      <div style={{ fontSize: 12, color: INK2, lineHeight: 1.3 }}>{v == null ? 'not available' : unit}</div>
    </div>
  )
}

const sectionTitle: CSSProperties = { fontSize: 13, fontWeight: 700, margin: 0 }

export default function ViewerBehaviourCard({ ep, attention }: { ep?: Engagement | null; attention?: ViewerAttention | null }) {
  const [m, setM] = useState<Measured | null | undefined>(undefined)
  useEffect(() => {
    if (!getToken()) { setM(null); return }
    authFetch(apiUrl('/instagram/measured'))
      .then(r => (r.ok ? r.json() : null))
      .then((j: Measured | null) => setM(j && j.connected ? j : null))
      .catch(() => setM(null))
  }, [])
  const unavailable = !ep || ep.available === false || ep.ecr == null || ep.nawp == null
  const showEstimate = !unavailable && (ep!.source === 'evqa' || (ep!.source === 'qwen_zero_shot' && SHOW_QWEN_CARD))
  const n = m?.reels ?? 0
    return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16, marginBottom: 16 }}>
      <div className="p-card p-fade" style={{ padding: 18 }} data-testid="viewer-behaviour">
        <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Viewer behaviour</h2>

        {m ? (
          <div data-testid="measured" style={{ marginTop: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 4 }}>
              <h3 style={sectionTitle}>Your account so far <span style={{ fontWeight: 500, color: '#55524b' }}>(measured)</span></h3>
              <span style={tag}>{m.isFixture ? 'Test data' : 'Measured'}</span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8, marginTop: 8 }} data-testid="m-tiles">
              <Tile testid="m-watched" label="Usually watched" value={m.avgWatchThrough == null ? null : m.avgWatchThrough * 100} unit="% of a reel" />
              <Tile testid="m-skipped" label="Skipped early" value={m.avgSkipRate} unit="% of views" />
              <Tile testid="m-reach" label="Typical reach" value={m.medianReach} unit="accounts" />
            </div>
            <p className="p-note" style={{ margin: '8px 0 0' }} data-testid="m-basis">
              Based on your last {n} reel{n === 1 ? '' : 's'}
              {n < SMALL_SAMPLE_BELOW && <span style={{ ...tag, marginLeft: 8, color: '#1f1f1f', borderColor: '#1f1f1f' }} data-testid="m-small">small sample</span>}
              {m.isFixture ? ' · TEST DATA from the development fixture' : ''}
              {m.avgWatchThrough != null && m.partialReels ? ` · ${m.partialReels} reel${m.partialReels === 1 ? '' : 's'} had no watch time from Instagram` : ''}
            </p>
          </div>
        ) : m === null ? (
          <p style={{ ...sub, color: '#1f1f1f', fontSize: 14, margin: '12px 0 0' }} data-testid="connect-line">
            <Link to="/creator-dna" style={{ color: '#1f1f1f', fontWeight: 700 }}>Connect Instagram to see viewer behaviour measured from your own reels</Link>
          </p>
        ) : <p style={{ ...sub, margin: '12px 0 0' }}>Loading…</p>}

        {attention && <AttentionStrip a={attention} />}

        {showEstimate && (
          <div style={{ borderTop: '1px solid #eee', marginTop: 16, paddingTop: 12 }} data-testid="estimate">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 13, fontWeight: 700 }}>Estimate for this video</span>
              <span style={tag}>{SOURCE_LABEL[ep!.source] || ep!.source}</span>
            </div>
            <p style={sub}>{SUBTITLE[ep!.source] || 'Estimate, not measured watch data'}</p>
            <Bar label="Hook Hold Rate" value={ep!.ecr as number} caption={HOOK[ep!.hook_risk || 'medium']} note="% of viewers predicted to watch past the first 5 seconds" />
            <Bar label="Average Watch Rate" value={ep!.nawp as number} caption={RETAIN[ep!.retention_risk || 'medium']} note="% of the video the average viewer is predicted to watch" />
          </div>
        )}
      </div>
      {ep?.qalign && <QualityPanel q={ep.qalign} />}
    </div>
  )
}
