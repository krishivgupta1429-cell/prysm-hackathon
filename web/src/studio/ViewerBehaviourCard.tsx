// Built during the Meta Global AI Developer Hackathon (new work).
// "Predicted Viewer Behaviour". With Instagram data imported it shows the creator's MEASURED averages (watch-through, skip rate, reach)
// from their own reels, labelled "measured from your account". Without it, no percentage bars: a line that links to the connect flow.
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

const box: CSSProperties = { border: '1px solid #e5e5e5', borderRadius: 16, padding: 20, background: '#fff', color: '#0a0a0a' }
const tag: CSSProperties = { fontSize: 9, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', padding: '2px 8px', border: '1px solid #d4d4d4', borderRadius: 999, color: '#737373', whiteSpace: 'nowrap' }
const sub: CSSProperties = { fontSize: 12, color: '#737373', margin: '4px 0 16px', lineHeight: 1.4 }
const track: CSSProperties = { height: 8, background: '#f0f0f0', borderRadius: 999, overflow: 'hidden' }

function Bar({ label, value, caption, note }: { label: string; value: number; caption: string; note: string }) {
  const pct = Math.max(0, Math.min(100, value))
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
        <span style={{ fontSize: 13, color: '#525252' }}>{label}</span>
        <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 15, fontWeight: 800 }}>{Math.round(pct)}%</span>
          <span style={tag}>{caption}</span>
        </span>
      </div>
      <div style={track}><div style={{ width: `${pct}%`, height: '100%', background: '#0a0a0a', borderRadius: 999 }} /></div>
      <p style={{ fontSize: 11, color: '#a3a3a3', margin: '4px 0 0' }}>{note}</p>
    </div>
  )
}

function QualityPanel({ q }: { q: QAlign }) {
  const pos = (v: number) => `${Math.max(0, Math.min(100, ((v - 1) / 4) * 100))}%`
  return (
    <div style={{ ...box, background: '#fafafa' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <h3 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Visual quality signal</h3>
        <span style={tag}>Q-Align</span>
      </div>
      <p style={sub}>How good the picture looks, scored 1 to 5. Not a watch prediction.</p>
      <div style={{ fontSize: 28, fontWeight: 800, lineHeight: 1 }}>{q.band}</div>
      <div style={{ fontSize: 13, color: '#525252', margin: '6px 0 14px' }}>{q.quality.toFixed(1)} / 5 video quality</div>
      <div style={{ position: 'relative', height: 4, background: '#e5e5e5', borderRadius: 999 }}>
        <div style={{ position: 'absolute', top: -4, left: pos(q.quality), width: 12, height: 12, marginLeft: -6, background: '#0a0a0a', borderRadius: 999 }} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#a3a3a3', marginTop: 8 }}><span>Bad</span><span>Poor</span><span>Fair</span><span>Good</span><span>Excellent</span></div>
      <div style={{ fontSize: 12, color: '#737373', marginTop: 12 }}>Aesthetics {q.aesthetic.toFixed(1)} / 5</div>
    </div>
  )
}

export type Measured = {
  connected: boolean; isFixture?: boolean; reels?: number; username?: string
  avgWatchThrough?: number | null; watchThroughReels?: number; partialReels?: number; avgSkipRate?: number | null; medianReach?: number | null
}

function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: 13, color: '#525252' }}>{label}</span>
        <span style={{ fontSize: 18, fontWeight: 800 }}>{value}</span>
      </div>
      <p style={{ fontSize: 11, color: '#a3a3a3', margin: '2px 0 0' }}>{note}</p>
    </div>
  )
}

export default function ViewerBehaviourCard({ ep }: { ep?: Engagement | null }) {
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
  const none = (v: number | null | undefined, f: (x: number) => string) => (v == null ? 'not available' : f(v))
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16, marginBottom: 20 }}>
      <div style={{ ...box, gridColumn: 'span 1' }} data-testid="viewer-behaviour">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Predicted Viewer Behaviour</h2>
          {m && <span style={tag}>{m.isFixture ? 'Test data' : 'Measured'}</span>}
        </div>
        {m ? (
          <div data-testid="measured">
            <p style={sub}>Measured from your account: averages over your last {m.reels} imported reels, not a prediction for this video.{m.isFixture ? ' TEST DATA from the development fixture.' : ''}</p>
            <Stat label="Average watch-through" value={none(m.avgWatchThrough, v => `${Math.round(v * 100)}%`)} note={m.watchThroughReels ? `of the reel's length, over ${m.watchThroughReels} reels with watch time${m.partialReels ? `; ${m.partialReels} reel${m.partialReels === 1 ? ' has' : 's have'} no video file from Instagram and no watch-through` : ''}` : 'Instagram gave no watch time for these reels'} />
            <Stat label="Average skip rate" value={none(m.avgSkipRate, v => `${Math.round(v * 100)}%`)} note="share of views that skipped early (Instagram's estimate)" />
            <Stat label="Typical reach" value={none(m.medianReach, v => Math.round(v).toLocaleString())} note="median accounts reached per reel" />
          </div>
        ) : m === null ? (
          <p style={{ ...sub, color: '#0a0a0a', fontSize: 14, margin: '12px 0 0' }} data-testid="connect-line">
            <Link to="/creator-dna" style={{ color: '#0a0a0a', fontWeight: 700 }}>Connect Instagram to see viewer behaviour measured from your own reels</Link>
          </p>
        ) : <p style={sub}>Loading…</p>}
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
