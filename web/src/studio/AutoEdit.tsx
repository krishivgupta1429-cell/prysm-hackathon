// Built during the Meta Global AI Developer Hackathon (new work).
// "Auto-edit this reel": starts a verified edit of an UPLOADED video, shows the live agent trace, a before/after player, every change
// with its kept/reverted verdict + reason + a toggle, a style preset, a plain-English revision box, the verification table (always
// labelled AI-estimated), manual tips and downloads. Rendered only when the backend has AUTO_EDIT_ENABLED on (the routes answer 404
// otherwise and this component renders nothing). Black-and-white, inline styles, mobile friendly.
import { useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { apiUrl } from '@/lib/api'
import { getToken } from '@/lib/auth'

type Change = { id: string; type: string; group: string; label: string; reason: string; target_parameter: string; enabled: boolean; verdict: string; verdict_reasons: string[]; user_override: boolean }
type Group = { label: string; status: string; reasons: string[]; target: string; scores?: { before: Record<string, number>; after: Record<string, number>; label?: string } | null; critic?: { verdict: string; reasons: string[]; flags: { flag: string; detail?: string }[]; mode: string } | null; metrics_before?: Metrics; metrics_after?: Metrics; objective?: { checks: { name: string; ok: boolean; detail: string }[]; ok: boolean } }
type Metrics = { duration_s?: number; lufs?: number | null; true_peak_db?: number | null; silence_ratio?: number; cut_count?: number; avg_shot_s?: number; black_frames?: number; frozen_frames?: number }
type Tip = { step_id?: string; parameter: string; tip: string; why_manual?: string }
type State = {
  status: string; phase: string; preset: string; presets: string[]; running: boolean; error?: string | null; trace: { t: string; kind: string; msg: string }[]
  changes: Change[]; groups: Record<string, Group>; manual_tips: Tip[]; revisions: { text: string; summary: string[] }[]; revisions_left: number
  baseline?: { metrics: Metrics } | null
  final?: { metrics: Metrics; overall_scores?: { before: Record<string, number>; after: Record<string, number>; notes?: string } | null; partial?: boolean; not_rescored?: boolean; objective_checks?: { group: string; name: string; ok: boolean; detail: string }[]; has_captions?: boolean } | null
  cost?: { total_usd: number }
}
type Resp = { eligible: boolean; reason: string; state: State | null }

const box: CSSProperties = { border: '1px solid #e5e5e5', borderRadius: 16, padding: 20, background: '#fff', color: '#0a0a0a', marginBottom: 20 }
const tag: CSSProperties = { fontSize: 9, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', padding: '2px 8px', border: '1px solid #d4d4d4', borderRadius: 999, color: '#737373', whiteSpace: 'nowrap' }
const btn: CSSProperties = { background: '#0a0a0a', color: '#fff', border: '1px solid #0a0a0a', borderRadius: 999, padding: '10px 18px', fontSize: 13, fontWeight: 600, cursor: 'pointer' }
const btn2: CSSProperties = { ...btn, background: '#fff', color: '#0a0a0a', border: '1px solid #d4d4d4' }
const h3: CSSProperties = { fontSize: 11, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: '#737373', margin: '22px 0 8px' }
const muted: CSSProperties = { fontSize: 12, color: '#737373', lineHeight: 1.45 }

const auth = () => ({ Authorization: `Bearer ${getToken() || ''}` })
const jhdr = () => ({ ...auth(), 'Content-Type': 'application/json' })
const fmt = (n?: number | null, d = 1) => (n == null ? '–' : n.toFixed(d))
const SCORED = ['Hook & Opening', 'Retention & Pacing', 'Shareability', 'Audio & Music', 'Trend Alignment', 'Visual Quality & Originality', 'On-Screen Text']

function VideoBox({ title, path, version }: { title: string; path: string; version: number }) {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    let dead = false, made: string | null = null
    setUrl(null)
    fetch(apiUrl(path), { headers: auth() }).then(r => (r.ok ? r.blob() : null)).then(b => { if (b && !dead) { made = URL.createObjectURL(b); setUrl(made) } }).catch(() => {})
    return () => { dead = true; if (made) URL.revokeObjectURL(made) }
  }, [path, version])
  return (
    <div style={{ flex: '1 1 200px', minWidth: 0 }}>
      <div style={{ ...muted, fontWeight: 600, marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.1em', fontSize: 10 }}>{title}</div>
      <div style={{ background: '#0a0a0a', borderRadius: 12, aspectRatio: '9 / 16', overflow: 'hidden', maxHeight: 460 }}>
        {url ? <video src={url} controls playsInline style={{ width: '100%', height: '100%', objectFit: 'contain' }} /> : <div style={{ color: '#a3a3a3', fontSize: 12, padding: 16 }}>Loading…</div>}
      </div>
    </div>
  )
}

export default function AutoEdit({ analysisId }: { analysisId?: string }) {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [data, setData] = useState<Resp | null>(null)
  const [preset, setPreset] = useState('clean')
  const [text, setText] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [version, setVersion] = useState(0)
  const timer = useRef<number | undefined>(undefined)
  const base = `/prysm/auto-edit/${analysisId}`

  const load = useCallback(async () => {
    if (!analysisId) return
    const r = await fetch(apiUrl(`${base}/status`), { headers: auth() })
    if (r.ok) { const j: Resp = await r.json(); setData(j); if (j.state) setPreset(j.state.preset) }
  }, [analysisId, base])

  useEffect(() => {
    if (!analysisId || !getToken()) return
    fetch(apiUrl('/prysm/auto-edit/available'), { headers: auth() }).then(r => { setEnabled(r.ok); if (r.ok) load() }).catch(() => setEnabled(false))
  }, [analysisId, load])

  const running = !!data?.state?.running
  useEffect(() => {
    window.clearInterval(timer.current)
    if (running) timer.current = window.setInterval(load, 2500)
    return () => window.clearInterval(timer.current)
  }, [running, load])
  const wasRunning = useRef(false)
  useEffect(() => { if (wasRunning.current && !running) setVersion(v => v + 1); wasRunning.current = running }, [running])

  async function call(method: string, path: string, body?: unknown) {
    setErr(''); setBusy(true)
    try {
      const r = await fetch(apiUrl(`${base}${path}`), { method, headers: jhdr(), body: body ? JSON.stringify(body) : undefined })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) { setErr(typeof j.detail === 'string' ? j.detail : 'Something went wrong.'); return false }
      if (method !== 'DELETE') setData(d => ({ eligible: true, reason: '', ...(d || {}), state: j }))
      return true
    } finally { setBusy(false) }
  }
  async function download(kind: string, name: string) {
    const r = await fetch(apiUrl(`${base}/download/${kind}`), { headers: auth() })
    if (!r.ok) { setErr('That file is not available for this edit.'); return }
    const a = document.createElement('a'); a.href = URL.createObjectURL(await r.blob()); a.download = name; a.click()
  }

  if (enabled !== true || !data) return null
  const st = data.state

  if (!st) {
    return (
      <div style={box} data-testid="auto-edit">
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}><span style={{ fontSize: 16, fontWeight: 700 }}>Auto-edit this reel</span><span style={tag}>New</span></div>
        <p style={{ ...muted, margin: '6px 0 14px' }}>Prysm applies its own fixes to your video: tighter pacing, captions, hook text, zoom punches and loudness. Each change is checked and automatically undone if it does not help. Your original is never modified.</p>
        {data.eligible ? (
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
            <select value={preset} onChange={e => setPreset(e.target.value)} style={{ ...btn2, padding: '9px 14px' }} aria-label="Style preset">
              {['clean', 'energetic', 'minimal'].map(p => <option key={p} value={p}>{p[0].toUpperCase() + p.slice(1)}</option>)}
            </select>
            <button style={{ ...btn, opacity: busy ? 0.5 : 1 }} disabled={busy} onClick={async () => { if (await call('POST', '/start', { preset })) load() }}>Auto-edit this reel</button>
          </div>
        ) : <p style={{ ...muted, margin: 0 }}>{data.reason}</p>}
        {err && <p style={{ ...muted, color: '#b91c1c' }}>{err}</p>}
      </div>
    )
  }

  const done = !st.running && st.final
  const m0 = st.baseline?.metrics, m1 = st.final?.metrics
  const overall = st.final?.overall_scores
  return (
    <div style={box} data-testid="auto-edit">
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 16, fontWeight: 700 }}>Auto-edit</span>
          <span style={tag}>{st.running ? `working · ${st.phase.split(':')[0]}` : st.status === 'done' ? 'done' : st.status}</span>
        </div>
        {!st.running && <div style={{ display: 'flex', gap: 8 }}>
          <button style={btn2} disabled={busy} onClick={async () => { if (await call('POST', '/start', { preset: st.preset })) load() }}>Run again</button>
          <button style={btn2} disabled={busy} onClick={async () => { if (window.confirm('Delete the edited video and the stored copy of your upload?') && await call('DELETE', '')) { setData(d => d && { ...d, state: null, eligible: false, reason: 'Deleted. The stored files for this video were removed.' }) } }}>Delete</button>
        </div>}
      </div>
      {st.error && <p style={{ ...muted, color: '#b91c1c', margin: '10px 0 0' }}>{st.status === 'stopped' ? st.error + ' The changes verified so far are kept.' : st.error}</p>}
      {err && <p style={{ ...muted, color: '#b91c1c' }}>{err}</p>}

      {/* live agent trace */}
      <h3 style={h3}>{st.running ? 'Live' : 'What happened'}</h3>
      <ol style={{ margin: 0, padding: 0, listStyle: 'none', maxHeight: st.running ? 'none' : 150, overflowY: 'auto' }} data-testid="auto-edit-trace">
        {st.trace.filter(t => t.kind !== 'info' || st.running).slice(-14).map((t, i, a) => (
          <li key={i} style={{ fontSize: 12, lineHeight: 1.5, color: i === a.length - 1 && st.running ? '#0a0a0a' : '#525252', display: 'flex', gap: 8 }}>
            <span style={{ width: 14, flex: 'none' }}>{t.kind === 'kept' ? '✓' : t.kind === 'reverted' ? '↩' : i === a.length - 1 && st.running ? '›' : '·'}</span><span>{t.msg}</span>
          </li>
        ))}
      </ol>

      {done && <>
        <h3 style={h3}>Before and after</h3>
        <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
          <VideoBox title="Before (your upload)" path={`${base}/download/original`} version={0} />
          <VideoBox title="After" path={`${base}/download/mp4`} version={version} />
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
          <button style={btn} onClick={() => download('mp4', 'prysm-edit.mp4')}>Download MP4</button>
          {st.final?.has_captions && <><button style={btn2} onClick={() => download('srt', 'captions.srt')}>Captions (SRT)</button><button style={btn2} onClick={() => download('ass', 'captions.ass')}>Captions (ASS)</button></>}
          <button style={btn2} onClick={() => download('cover', 'cover.jpg')}>Cover image</button>
          <button style={btn2} onClick={() => download('edl', 'edit-list.json')}>Edit list (JSON)</button>
        </div>

        <h3 style={h3}>Changes</h3>
        {st.changes.length === 0 && <p style={muted}>The plan had no changes that Prysm can make automatically. See the manual tips below.</p>}
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {st.changes.map(c => (
            <li key={c.id} style={{ display: 'flex', gap: 12, alignItems: 'flex-start', padding: '10px 0', borderTop: '1px solid #f0f0f0' }}>
              <input type="checkbox" checked={c.enabled} disabled={busy || st.running} onChange={e => call('POST', '/toggle', { op_id: c.id, enabled: e.target.checked })} aria-label={`Toggle: ${c.label}`} style={{ marginTop: 4, width: 18, height: 18, accentColor: '#0a0a0a' }} />
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600, wordBreak: 'break-word' }}>{c.label}</div>
                <div style={muted}>{c.reason}{c.target_parameter ? ` · ${c.target_parameter}` : ''}</div>
                <div style={{ ...muted, marginTop: 2 }}>
                  <span style={{ ...tag, color: c.verdict === 'kept' ? '#0a0a0a' : '#737373', borderColor: c.verdict === 'kept' ? '#0a0a0a' : '#d4d4d4' }}>{c.verdict === 'kept' ? 'kept' : c.verdict === 'reverted' ? 'reverted' : c.verdict}</span>
                  {c.user_override && <span style={{ ...tag, marginLeft: 6 }}>your choice</span>}
                  <span style={{ marginLeft: 8 }}>{c.verdict_reasons.join(' · ')}</span>
                </div>
              </div>
            </li>
          ))}
        </ul>

        <h3 style={h3}>Style and requests</h3>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
          {(st.presets || []).map(p => <button key={p} disabled={busy || st.running} onClick={() => call('POST', '/preset', { preset: p })} style={p === st.preset ? btn : btn2}>{p[0].toUpperCase() + p.slice(1)}</button>)}
        </div>
        <form onSubmit={async e => { e.preventDefault(); if (text.trim() && await call('POST', '/revise', { text })) setText('') }} style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <input value={text} onChange={e => setText(e.target.value)} placeholder="e.g. bigger captions, less zoom, keep the intro" maxLength={300} aria-label="Ask for a change" style={{ flex: '1 1 220px', minWidth: 0, border: '1px solid #d4d4d4', borderRadius: 999, padding: '10px 16px', fontSize: 13 }} />
          <button type="submit" style={btn} disabled={busy || st.running || st.revisions_left <= 0}>Apply</button>
        </form>
        <div style={{ ...muted, marginTop: 6 }}>{st.revisions_left} of 5 requests left. {st.revisions.length > 0 && 'Done: ' + st.revisions.map(r => `“${r.text}”`).join(', ')}</div>

        <h3 style={h3}>Verification <span style={{ ...tag, marginLeft: 6 }}>AI-estimated</span></h3>
        <p style={{ ...muted, margin: '0 0 8px' }}>Scores are AI estimates from the frames and transcript, not measured viewership. Numbers from tools (loudness, length, silence, cuts) are measured.</p>
        {st.final?.not_rescored && <p style={{ ...muted, margin: '0 0 8px' }}>The latest change was re-checked by tools only; the AI re-score below is from the original run.</p>}
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, minWidth: 420 }}>
            <thead><tr style={{ textAlign: 'left', color: '#737373' }}><th style={{ padding: '6px 4px' }}>Measured by tools</th><th>Before</th><th>After</th></tr></thead>
            <tbody>
              {([['Length (s)', 'duration_s', 1], ['Loudness (LUFS)', 'lufs', 1], ['True peak (dBTP)', 'true_peak_db', 1], ['Silence share', 'silence_ratio', 2], ['Cuts', 'cut_count', 0], ['Average shot (s)', 'avg_shot_s', 1]] as [string, keyof Metrics, number][]).map(([l, k, d]) => (
                <tr key={k} style={{ borderTop: '1px solid #f0f0f0' }}><td style={{ padding: '6px 4px' }}>{l}</td><td>{fmt(m0?.[k] as number, d)}</td><td>{fmt(m1?.[k] as number, d)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        {overall && (
          <div style={{ overflowX: 'auto', marginTop: 12 }}>
            <p style={{ ...muted, margin: '0 0 6px' }}>This estimate is noisy (a few points up or down, sometimes more). A change in a parameter the edit did not touch is estimator noise, not an effect of the edit.</p>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, minWidth: 420 }}>
              <thead><tr style={{ textAlign: 'left', color: '#737373' }}><th style={{ padding: '6px 4px' }}>Parameter (AI-estimated)</th><th>Before</th><th>After</th><th>Change</th></tr></thead>
              <tbody>
                {SCORED.filter(k => k in overall.before && k in overall.after).map(k => { const d = overall.after[k] - overall.before[k]; return (
                  <tr key={k} style={{ borderTop: '1px solid #f0f0f0' }}><td style={{ padding: '6px 4px' }}>{k}</td><td>{overall.before[k]}</td><td>{overall.after[k]}</td><td style={{ fontWeight: d ? 700 : 400 }}>{d > 0 ? '+' : ''}{d}</td></tr>
                ) })}
              </tbody>
            </table>
          </div>
        )}
        <details style={{ marginTop: 12 }}>
          <summary style={{ ...muted, cursor: 'pointer', fontWeight: 600 }}>Checks per group</summary>
          {Object.entries(st.groups).map(([g, v]) => (
            <div key={g} style={{ margin: '10px 0' }}>
              <div style={{ fontSize: 12, fontWeight: 700 }}>{v.label} — {v.status}{v.critic ? ` · critic: ${v.critic.verdict}` : ''}</div>
              {v.critic?.reasons?.length ? <div style={muted}>Critic: {v.critic.reasons.join(' · ')}</div> : null}
              {(v.objective?.checks || []).map((c, i) => <div key={i} style={{ ...muted, color: c.ok ? '#525252' : '#b91c1c' }}>{c.ok ? '✓' : '✗'} {c.name}: {c.detail}</div>)}
            </div>
          ))}
        </details>

        {st.manual_tips.length > 0 && <>
          <h3 style={h3}>Do these yourself</h3>
          {(() => { const li = (t: Tip, i: number) => <li key={i} style={{ fontSize: 12, lineHeight: 1.5, color: '#525252', marginBottom: 4 }}>{t.tip}{t.why_manual ? <span style={{ color: '#a3a3a3' }}> ({t.why_manual})</span> : null}</li>
            return <>
              <ul style={{ margin: 0, paddingLeft: 18 }}>{st.manual_tips.slice(0, 6).map(li)}</ul>
              {st.manual_tips.length > 6 && <details><summary style={{ ...muted, cursor: 'pointer', fontWeight: 600, marginTop: 6 }}>{st.manual_tips.length - 6} more</summary><ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>{st.manual_tips.slice(6).map((t, i) => li(t, i + 6))}</ul></details>}
            </>
          })()}
        </>}
      </>}
    </div>
  )
}
