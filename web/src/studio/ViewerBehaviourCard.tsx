// Built during the Meta Global AI Developer Hackathon (new work).
// "Predicted Viewer Behaviour" for the Modal-served models (source "evqa" or "qwen_zero_shot") plus the separate
// "Visual quality signal" from Q-Align. Black-and-white, inline styles. The subtitle says honestly where the numbers
// come from; when the prediction is unavailable no bars are drawn. Q-Align is a picture-quality score from 1 to 5,
// never a percentage and never a watch prediction.
import type { CSSProperties } from 'react'

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

export default function ViewerBehaviourCard({ ep }: { ep: Engagement }) {
  const unavailable = ep.available === false || ep.ecr == null || ep.nawp == null
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16, marginBottom: 20 }}>
      <div style={{ ...box, gridColumn: 'span 1' }} data-testid="viewer-behaviour">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Predicted Viewer Behaviour</h2>
          {!unavailable && <span style={tag}>{SOURCE_LABEL[ep.source] || ep.source}</span>}
        </div>
        {unavailable ? (
          <>
            <p style={{ ...sub, color: '#0a0a0a', fontWeight: 600, fontSize: 14, margin: '12px 0 4px' }}>Prediction unavailable</p>
            <p style={{ ...sub, margin: 0 }}>{ep.reason || 'The prediction model did not answer for this video.'}</p>
          </>
        ) : (
          <>
            <p style={sub}>{SUBTITLE[ep.source] || 'Estimate, not measured watch data'}</p>
            <Bar label="Hook Hold Rate" value={ep.ecr as number} caption={HOOK[ep.hook_risk || 'medium']} note="% of viewers predicted to watch past the first 5 seconds" />
            <Bar label="Average Watch Rate" value={ep.nawp as number} caption={RETAIN[ep.retention_risk || 'medium']} note="% of the video the average viewer is predicted to watch" />
          </>
        )}
      </div>
      {ep.qalign && <QualityPanel q={ep.qalign} />}
    </div>
  )
}
