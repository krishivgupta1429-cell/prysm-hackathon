// Built during the Meta Global AI Developer Hackathon (new work).
// Dev-only page (route /dev/dino, dev server with VITE_DEV_DINO=1, backend with DEV_DINO=1, needs login). DINOv2 is an
// existing Meta model (Apache 2.0); this page shows the new integration: the attention strip (visual change, audio energy,
// audio change, all estimates), the before/after render check per segment, and the user's own reels that look most like
// an analysis. Black and white, inline styles, like the viewer-behaviour card. Nothing here is invented: an unavailable part
// shows its reason instead of numbers.
import { useEffect, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { apiUrl } from '@/lib/api'
import { authFetch, isLoggedIn } from '@/lib/auth'

type Span = { start: number; end: number; duration?: number }
type Part = { available?: boolean; reason?: string }
type Strip = {
  label: string; bin_s: number; duration: number; times: number[]
  visual_change: (number | null)[] | null; audio_energy: (number | null)[] | null; audio_change: (number | null)[] | null
  visual: Part & { static_segments?: Span[]; scale?: number; static_threshold_raw?: number }
  energy: Part & { db?: number[] }; change: Part
  beats: Part & { onsets?: number[]; beats?: number[]; tempo_bpm?: number | null }
  planner: { static_stretches: Span[]; loudness_dips: (Span & { min_db: number })[]; onset_times: number[]; beat_times: number[]; tempo_bpm: number | null }
  usage?: { frames?: number; latency_s?: number; gpu_seconds?: number; est_cost_usd?: number; cold?: boolean; gpu?: string }
}
type Seg = { start: number; end: number; similarity: number | null; min_similarity: number | null; flagged: boolean; reason?: string | null }
type Integrity = { available: boolean; reason?: string; threshold: number; segments: Seg[]; flagged_count: number; mean_similarity: number | null; min_similarity: number | null; note: string; usage?: Strip['usage'] }
type Reel = { media_id: string; similarity: number; metrics: Record<string, unknown> | null; metrics_note?: string | null }
type Nearest = { available: boolean; reason?: string; label: string; note?: string; reels: Reel[]; compared_with?: number }

const box: CSSProperties = { border: '1px solid #e5e5e5', borderRadius: 16, padding: 20, background: '#fff', color: '#0a0a0a' }
const tag: CSSProperties = { fontSize: 9, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', padding: '2px 8px', border: '1px solid #d4d4d4', borderRadius: 999, color: '#737373', whiteSpace: 'nowrap' }
const sub: CSSProperties = { fontSize: 12, color: '#737373', margin: '4px 0 16px', lineHeight: 1.4 }
const btn: CSSProperties = { padding: '8px 16px', fontSize: 13, fontWeight: 600, border: '1px solid #0a0a0a', borderRadius: 999, background: '#0a0a0a', color: '#fff', cursor: 'pointer' }
const btnOff: CSSProperties = { ...btn, background: '#fff', color: '#a3a3a3', border: '1px solid #d4d4d4', cursor: 'default' }
const muted: CSSProperties = { fontSize: 11, color: '#a3a3a3', margin: '6px 0 0', lineHeight: 1.4 }

function Card({ title, badge, subtitle, children }: { title: string; badge: string; subtitle: string; children: ReactNode }) {
  return (
    <section style={box}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <h2 style={{ fontSize: 15, fontWeight: 700, margin: 0 }}>{title}</h2>
        <span style={tag}>{badge}</span>
      </div>
      <p style={sub}>{subtitle}</p>
      {children}
    </section>
  )
}

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`

/** One row of bars on the 0.5 s grid. Static spans are shaded behind the bars; ticks are onset times. */
function Row({ label, values, dur, spans, ticks, unavailable }: { label: string; values: (number | null)[] | null; dur: number; spans?: Span[]; ticks?: number[]; unavailable?: string }) {
  const n = values?.length || 1
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
        <span style={{ fontSize: 12, color: '#525252' }}>{label}</span>
        <span style={tag}>estimate</span>
      </div>
      {values ? (
        <svg viewBox={`0 0 ${n} 40`} preserveAspectRatio="none" style={{ width: '100%', height: 56, display: 'block', background: '#fafafa', borderRadius: 8 }} role="img" aria-label={label}>
          {(spans || []).map((s, i) => <rect key={i} x={(s.start / dur) * n} y={0} width={Math.max(0.2, ((s.end - s.start) / dur) * n)} height={40} fill="#e5e5e5" />)}
          {values.map((v, i) => v == null ? null : <rect key={i} x={i + 0.1} y={40 - v * 38 - 1} width={0.8} height={v * 38 + 1} fill="#0a0a0a" />)}
          {(ticks || []).map((t, i) => <line key={i} x1={(t / dur) * n} x2={(t / dur) * n} y1={0} y2={5} stroke="#737373" strokeWidth={0.12} />)}
        </svg>
      ) : (
        <div style={{ ...muted, margin: 0, padding: 12, background: '#fafafa', borderRadius: 8 }}>Not available: {unavailable || 'no reason given'}</div>
      )}
    </div>
  )
}

function useStatus() {
  const [s, setS] = useState<string>('checking…')
  useEffect(() => {
    authFetch(apiUrl('/dino/status')).then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(d => setS(d.backend === 'modal' ? (d.health?.ok ? 'DINOv2 service: up' : `DINOv2 service: not reachable (${d.health?.reason || '?'})`) : 'DINOv2 is off (DINO_BACKEND=off): the visual parts will say unavailable'))
      .catch(e => setS(`Backend not reachable or DEV_DINO is off (${e.message})`))
  }, [])
  return s
}

function Usage({ u }: { u?: Strip['usage'] }) {
  if (!u || !u.frames) return null
  return <p style={muted}>{u.frames} frames · {u.latency_s}s · {u.cold ? 'cold start' : 'warm'} · GPU {u.gpu_seconds}s · about ${Number(u.est_cost_usd).toFixed(4)} (estimate) · {u.gpu}</p>
}

function CurveCard() {
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [strip, setStrip] = useState<Strip | null>(null)
  const run = async () => {
    if (!file) return
    setBusy(true); setErr(''); setStrip(null)
    try {
      const fd = new FormData(); fd.append('video', file)
      const r = await authFetch(apiUrl('/dino/curve'), { method: 'POST', body: fd })
      if (!r.ok) throw new Error(`${r.status} ${(await r.json().catch(() => ({}))).detail || ''}`)
      setStrip(await r.json())
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const statics = strip?.visual?.available ? strip.visual.static_segments || [] : []
  return (
    <Card title="Attention strip" badge="DINOv2 + audio" subtitle="Visual change (DINOv2 frames, 4 per second), audio energy (RMS loudness) and audio change (spectral flux), one bar per half second. Grey bands: nothing changes on screen for 1.5 s or more. All estimates.">
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 16 }}>
        <input type="file" accept="video/*" aria-label="Video for the attention strip" onChange={e => setFile(e.target.files?.[0] || null)} style={{ fontSize: 12, maxWidth: '100%' }} />
        <button style={file && !busy ? btn : btnOff} disabled={!file || busy} onClick={run}>{busy ? 'Working…' : 'Analyse'}</button>
      </div>
      {err && <p style={{ ...muted, color: '#0a0a0a' }} role="alert">Failed: {err}</p>}
      {strip && (
        <div data-testid="strip">
          <Row label="Visual change (estimate)" values={strip.visual_change} dur={strip.duration} spans={statics} unavailable={strip.visual.reason} />
          <Row label="Audio energy (estimate)" values={strip.audio_energy} dur={strip.duration} spans={statics} unavailable={strip.energy.reason} />
          <Row label="Audio change (estimate)" values={strip.audio_change} dur={strip.duration} spans={statics} ticks={strip.planner.onset_times} unavailable={strip.change.reason} />
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#a3a3a3' }}><span>0:00</span><span>{fmt(strip.duration / 2)}</span><span>{fmt(strip.duration)}</span></div>
          <p style={muted}>Ticks on the audio-change row: onset times{strip.planner.tempo_bpm ? ` · tempo about ${strip.planner.tempo_bpm} bpm` : ''}.</p>
          <div style={{ marginTop: 12, fontSize: 12, lineHeight: 1.6 }}>
            <strong>Static stretches:</strong> {strip.visual.available ? (statics.length ? statics.map(s => `${s.start}–${s.end}s`).join(', ') : 'none found') : `not available (${strip.visual.reason})`}<br />
            <strong>Loudness dips:</strong> {strip.energy.available ? (strip.planner.loudness_dips.length ? strip.planner.loudness_dips.map(s => `${s.start}–${s.end}s (${s.min_db} dB)`).join(', ') : 'none found') : `not available (${strip.energy.reason})`}<br />
            <strong>Onsets:</strong> {strip.beats.available ? `${strip.planner.onset_times.length} found, ${strip.planner.beat_times.length} beats` : `not available (${strip.beats.reason})`}
          </div>
          <Usage u={strip.usage} />
        </div>
      )}
    </Card>
  )
}

function IntegrityCard() {
  const [before, setBefore] = useState<File | null>(null)
  const [after, setAfter] = useState<File | null>(null)
  const [edl, setEdl] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [res, setRes] = useState<Integrity | null>(null)
  const run = async () => {
    if (!before || !after) return
    setBusy(true); setErr(''); setRes(null)
    try {
      const fd = new FormData(); fd.append('before', before); fd.append('after', after)
      if (edl) fd.append('edl', await edl.text())
      const r = await authFetch(apiUrl('/dino/integrity'), { method: 'POST', body: fd })
      if (!r.ok) throw new Error(`${r.status} ${(await r.json().catch(() => ({}))).detail || ''}`)
      setRes(await r.json())
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const end = res?.segments.length ? res.segments[res.segments.length - 1].end : 1
  return (
    <Card title="Render check" badge="DINOv2" subtitle="Each stretch of the edited video is compared with its source at matching timestamps. Flags a broken render, a wrong segment, a black or frozen frame, a heavy crop or over-zoom.">
      <div style={{ display: 'grid', gap: 8, marginBottom: 16 }}>
        <label style={{ fontSize: 12 }}>Original <input type="file" accept="video/*" aria-label="Original video" onChange={e => setBefore(e.target.files?.[0] || null)} /></label>
        <label style={{ fontSize: 12 }}>Edited <input type="file" accept="video/*" aria-label="Edited video" onChange={e => setAfter(e.target.files?.[0] || null)} /></label>
        <label style={{ fontSize: 12 }}>Edit list, optional (JSON; needed when segments were cut) <input type="file" accept=".json,application/json" aria-label="Edit list" onChange={e => setEdl(e.target.files?.[0] || null)} /></label>
        <div><button style={before && after && !busy ? btn : btnOff} disabled={!before || !after || busy} onClick={run}>{busy ? 'Working…' : 'Check'}</button></div>
      </div>
      {err && <p style={{ ...muted, color: '#0a0a0a' }} role="alert">Failed: {err}</p>}
      {res && !res.available && <p style={muted}>Not available: {res.reason}</p>}
      {res?.available && (
        <div data-testid="integrity">
          <p style={{ fontSize: 13, margin: '0 0 8px', fontWeight: 700 }}>{res.flagged_count === 0 ? 'No segment flagged' : `${res.flagged_count} of ${res.segments.length} segments flagged`} <span style={{ ...tag, marginLeft: 6 }}>estimate</span></p>
          <svg viewBox={`0 0 ${end} 44`} preserveAspectRatio="none" style={{ width: '100%', height: 72, display: 'block', background: '#fafafa', borderRadius: 8 }} role="img" aria-label="Similarity per segment">
            {res.segments.map((s, i) => {
              const v = s.similarity ?? 0
              return <rect key={i} x={s.start + 0.05} y={44 - v * 42} width={Math.max(0.1, s.end - s.start - 0.1)} height={Math.max(0.5, v * 42)}
                fill={s.flagged ? '#fff' : '#0a0a0a'} stroke="#0a0a0a" strokeWidth={s.flagged ? 1.5 : 0} strokeDasharray={s.flagged ? '3 2' : undefined} vectorEffect="non-scaling-stroke" />
            })}
            <line x1={0} x2={end} y1={44 - res.threshold * 42} y2={44 - res.threshold * 42} stroke="#fff" strokeWidth={3} vectorEffect="non-scaling-stroke" />
            <line x1={0} x2={end} y1={44 - res.threshold * 42} y2={44 - res.threshold * 42} stroke="#737373" strokeWidth={1} strokeDasharray="4 3" vectorEffect="non-scaling-stroke" />
          </svg>
          <p style={muted}>Bar height = similarity to the source (0 to 1). Dashed line = threshold {res.threshold}. Hollow dashed bars = flagged. Mean {res.mean_similarity}, lowest {res.min_similarity}.</p>
          <ul style={{ margin: '10px 0 0', padding: 0, listStyle: 'none', fontSize: 12, lineHeight: 1.7 }}>
            {res.segments.map((s, i) => (
              <li key={i} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontWeight: s.flagged ? 700 : 400 }}>
                <span>{s.start.toFixed(1)}–{s.end.toFixed(1)}s</span>
                <span>{s.similarity == null ? 'n/a' : s.similarity.toFixed(3)}{s.flagged ? ' · flagged' : ''}</span>
              </li>
            ))}
          </ul>
          <p style={muted}>{res.note}</p>
          <Usage u={res.usage} />
        </div>
      )}
    </Card>
  )
}

function NearestCard() {
  const [id, setId] = useState('')
  const [video, setVideo] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [res, setRes] = useState<Nearest | null>(null)
  const run = async () => {
    setBusy(true); setErr(''); setRes(null)
    try {
      if (video) {
        const fd = new FormData(); fd.append('video', video)
        const e = await authFetch(apiUrl(`/dino/embed/${encodeURIComponent(id.trim())}`), { method: 'POST', body: fd })
        if (!e.ok) throw new Error(`embed ${e.status} ${(await e.json().catch(() => ({}))).detail || ''}`)
        const body = await e.json()
        if (body.available === false) { setRes({ available: false, reason: body.reason, label: '', reels: [] }); return }
      }
      const r = await authFetch(apiUrl(`/dino/nearest/${encodeURIComponent(id.trim())}`))
      if (!r.ok) throw new Error(`${r.status} ${(await r.json().catch(() => ({}))).detail || ''}`)
      setRes(await r.json())
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const ok = id.trim().length > 0 && !busy
  return (
    <Card title="Your own reels that look like this" badge="DINOv2" subtitle="Ranked by how similar the pictures look. Similar look does not mean similar results: each reel shows its own stored metrics, nothing is predicted.">
      <div style={{ display: 'grid', gap: 8, marginBottom: 16 }}>
        <input value={id} onChange={e => setId(e.target.value)} placeholder="Analysis id (yours)" aria-label="Analysis id" style={{ fontSize: 13, padding: '8px 12px', border: '1px solid #d4d4d4', borderRadius: 8 }} />
        <label style={{ fontSize: 12 }}>Video of that analysis, optional (stores its embedding first) <input type="file" accept="video/*" aria-label="Analysis video" onChange={e => setVideo(e.target.files?.[0] || null)} /></label>
        <div><button style={ok ? btn : btnOff} disabled={!ok} onClick={run}>{busy ? 'Working…' : 'Find'}</button></div>
      </div>
      {err && <p style={{ ...muted, color: '#0a0a0a' }} role="alert">Failed: {err}</p>}
      {res && !res.available && <p style={muted}>Not available: {res.reason}</p>}
      {res?.available && res.reels.length === 0 && <p style={muted}>{res.reason || 'Nothing to show.'}</p>}
      {res?.available && res.reels.length > 0 && (
        <div data-testid="nearest">
          {res.reels.map((r, i) => (
            <div key={r.media_id} style={{ borderTop: i ? '1px solid #f0f0f0' : undefined, padding: '10px 0' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 13, fontWeight: 700 }}>#{i + 1} · {r.media_id.startsWith('fixture_') ? `${r.media_id} (fixture, not a real reel)` : r.media_id}</span>
                <span style={tag}>{(r.similarity * 100).toFixed(1)}% alike</span>
              </div>
              <div style={{ height: 6, background: '#f0f0f0', borderRadius: 999, margin: '6px 0' }}><div style={{ width: `${Math.max(0, Math.min(100, r.similarity * 100))}%`, height: '100%', background: '#0a0a0a', borderRadius: 999 }} /></div>
              {r.metrics ? (
                <p style={{ fontSize: 12, color: '#525252', margin: 0 }}>
                  {Object.entries(r.metrics).filter(([k, v]) => v !== null && v !== undefined && k !== 'permalink').map(([k, v]) => `${k.replace(/_/g, ' ')}: ${typeof v === 'number' ? Math.round(v * 100) / 100 : String(v)}`).join(' · ') || 'No metrics stored.'}
                </p>
              ) : <p style={muted}>No metrics shown: {r.metrics_note}</p>}
            </div>
          ))}
          <p style={muted}>Compared with {res.compared_with} stored reels. {res.note}</p>
        </div>
      )}
    </Card>
  )
}

export default function DevDino() {
  const status = useStatus()
  if (!isLoggedIn()) return <div style={{ minHeight: '100vh', padding: 24 }} className="bg-background text-foreground font-body">Log in first (this page calls the backend as you), then reload.</div>
  return (
    <div className="bg-background text-foreground font-body" style={{ minHeight: '100vh' }}>
      <div style={{ maxWidth: 720, margin: '0 auto', padding: '24px 16px 64px', display: 'grid', gap: 16 }}>
        <header>
          <span style={tag}>DINOv2 · dev only</span>
          <h1 style={{ fontSize: 22, fontWeight: 800, margin: '10px 0 4px' }}>Visual and audio attention</h1>
          <p style={{ fontSize: 12, color: '#737373', margin: 0, lineHeight: 1.5 }}>Existing Meta model (Apache 2.0), new integration. {status}.</p>
        </header>
        <CurveCard />
        <IntegrityCard />
        <NearestCard />
      </div>
    </div>
  )
}
