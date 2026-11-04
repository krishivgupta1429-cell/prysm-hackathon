// Built during the Meta Global AI Developer Hackathon (new work).
// "Auto-edit this reel": starts a verified edit of an UPLOADED video, shows the live agent trace, a before/after player, every change
// with its kept/reverted verdict + reason + a toggle, a style preset, a plain-English revision box, the verification table (always
// labelled AI-estimated), manual tips and downloads. Rendered only when the backend has AUTO_EDIT_ENABLED on (the routes answer 404
// otherwise and this component renders nothing). Black-and-white, inline styles, mobile friendly.
import { useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { apiUrl } from '@/lib/api'
import { getToken } from '@/lib/auth'

type Change = { id: string; type: string; group: string; label: string; reason: string; target_parameter: string; enabled: boolean; verdict: string; verdict_reasons: string[]; user_override: boolean; risk?: string; finding?: string; effect_options?: string[] }
type Group = { label: string; status: string; risk?: string; reasons: string[]; target: string; critic?: { verdict: string; reasons: string[]; defects: { flag: string; severity: string; detail?: string }[] } | null; metrics_after?: Metrics; objective?: { checks: { name: string; ok: boolean; detail: string; warn?: boolean }[]; ok: boolean } }
type Metrics = { duration_s?: number; lufs?: number | null; true_peak_db?: number | null; silence_ratio?: number; cut_count?: number; avg_shot_s?: number; black_frames?: number; frozen_frames?: number }
type Tip = { step_id?: string; parameter: string; tip: string; why_manual?: string }
type State = {
  status: string; phase: string; preset: string; presets: string[]; running: boolean; error?: string | null; trace: { t: string; kind: string; msg: string }[]
  changes: Change[]; groups: Record<string, Group>; manual_tips: Tip[]; revisions: { text: string; summary: string[] }[]; revisions_left: number
  baseline?: { metrics: Metrics } | null
  final?: { metrics: Metrics; overall_scores?: { before: Record<string, number>; after: Record<string, number>; notes?: string } | null; partial?: boolean; not_rescored?: boolean; objective_checks?: { group: string; name: string; ok: boolean; detail: string }[]; has_captions?: boolean } | null
  cost?: { total_usd: number }
  packs?: Record<string, { label: string; headline: string; caption: string; accent: string; animation: string; for: string }>; intensities?: string[]
  style?: { pack?: string | null; font_headline?: string | null; font_caption?: string | null; animation?: string | null; intensity?: string; sfx?: boolean }
  style_choice?: { pack: string; pack_reason: string; fonts: { family: string; role: string; reason: string }[]; emphasis_count: number; notes: string[] } | null
  style_notes?: string[]
  decisions?: { area: string; text: string }[]; critic?: { mode?: string; verdict?: string; notes?: string } | null
  timing?: { total_s?: number; stages?: Record<string, number> }
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

type FontRow = { family: string; category: string; devanagari: boolean }
const CATS = ['all', 'display', 'sans', 'serif', 'rounded', 'handwriting', 'mono', 'devanagari']
const GF_LINK = 'gf-preview-'

function useFontPreview(families: string[]) {
  // live preview: the browser loads each family from Google Fonts, limited to the letters of its own name so each request is tiny
  useEffect(() => {
    families.slice(0, 14).forEach(f => {
      const id = GF_LINK + f.replace(/\W+/g, '-')
      if (document.getElementById(id)) return
      const l = document.createElement('link'); l.id = id; l.rel = 'stylesheet'
      l.href = `https://fonts.googleapis.com/css2?family=${encodeURIComponent(f).replace(/%20/g, '+')}&text=${encodeURIComponent(f + ' Aa Hello')}&display=swap`
      document.head.appendChild(l)
    })
  }, [families])
}

function FontPicker({ label, value, onPick, busy, auth: authFn }: { label: string; value?: string | null; onPick: (family: string) => void; busy: boolean; auth: () => Record<string, string> }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [cat, setCat] = useState('all')
  const [rows, setRows] = useState<FontRow[]>([])
  useEffect(() => {
    if (!open) return
    const h = window.setTimeout(() => {
      const u = `/prysm/auto-edit/fonts?q=${encodeURIComponent(q)}${cat !== 'all' ? `&category=${cat}` : ''}`
      fetch(apiUrl(u), { headers: authFn() }).then(r => (r.ok ? r.json() : { fonts: [] })).then(j => setRows(j.fonts || [])).catch(() => {})
    }, 200)
    return () => window.clearTimeout(h)
  }, [open, q, cat, authFn])
  useFontPreview(open ? rows.map(r => r.family) : value ? [value] : [])
  return (
    <div style={{ flex: '1 1 220px', minWidth: 0 }}>
      <div style={{ ...muted, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.1em', fontSize: 10, marginBottom: 4 }}>{label}</div>
      <button style={{ ...btn2, width: '100%', textAlign: 'left', fontFamily: value ? `'${value}', sans-serif` : undefined, fontSize: 15 }} onClick={() => setOpen(o => !o)} aria-expanded={open} aria-label={`${label} font: ${value || 'default'}`}>{value || 'Poppins'} <span style={{ ...muted, float: 'right' }}>{open ? '▲' : '▼'}</span></button>
      {open && (
        <div style={{ border: '1px solid #e5e5e5', borderRadius: 12, padding: 10, marginTop: 6, background: '#fff' }}>
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search Google Fonts" aria-label="Search fonts" style={{ width: '100%', boxSizing: 'border-box', border: '1px solid #d4d4d4', borderRadius: 999, padding: '8px 14px', fontSize: 13 }} />
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '8px 0' }}>
            {CATS.map(c => <button key={c} onClick={() => setCat(c)} style={{ ...tag, cursor: 'pointer', background: c === cat ? '#0a0a0a' : '#fff', color: c === cat ? '#fff' : '#737373' }}>{c}</button>)}
          </div>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, maxHeight: 260, overflowY: 'auto' }}>
            {rows.map(r => (
              <li key={r.family}><button disabled={busy} onClick={() => { onPick(r.family); setOpen(false) }} style={{ all: 'unset', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', gap: 8, width: '100%', padding: '7px 4px', borderTop: '1px solid #f0f0f0', boxSizing: 'border-box' }}>
                <span style={{ fontFamily: `'${r.family}', sans-serif`, fontSize: 17 }}>{r.family}</span>
                <span style={{ ...muted, fontSize: 10, whiteSpace: 'nowrap' }}>{r.category}{r.devanagari ? ' · हिन्दी' : ''}</span>
              </button></li>
            ))}
            {rows.length === 0 && <li style={muted}>No fonts found.</li>}
          </ul>
        </div>
      )}
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

        {(st.decisions || []).length > 0 && <>
          <h3 style={h3}>What was measured and decided</h3>
          <ul style={{ margin: 0, paddingLeft: 18 }}>{(st.decisions || []).map((d, i) => <li key={i} style={{ fontSize: 12, lineHeight: 1.5, color: '#525252' }}>{d.text}</li>)}</ul>
        </>}
        <h3 style={h3}>Changes</h3>
        {st.changes.length === 0 && <p style={muted}>The plan had no changes that Prysm can make automatically. See the manual tips below.</p>}
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {st.changes.map(c => (
            <li key={c.id} style={{ display: 'flex', gap: 12, alignItems: 'flex-start', padding: '10px 0', borderTop: '1px solid #f0f0f0' }}>
              <input type="checkbox" checked={c.enabled} disabled={busy || st.running} onChange={e => call('POST', '/toggle', { op_id: c.id, enabled: e.target.checked })} aria-label={`Toggle: ${c.label}`} style={{ marginTop: 4, width: 18, height: 18, accentColor: '#0a0a0a' }} />
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600, wordBreak: 'break-word' }}>{c.label}</div>
                <div style={muted}>{c.reason}{c.target_parameter ? ` · ${c.target_parameter}` : ''}</div>
                {c.finding ? <div style={muted}>Answers: {c.finding}</div> : null}
                {(c.effect_options || []).length > 1 && <select value={(c.label.match(/\((\w+),/) || [])[1] || ''} disabled={busy || st.running} onChange={e => call('POST', '/swap', { op_id: c.id, effect: e.target.value })} aria-label="Swap sound effect" style={{ ...btn2, padding: '3px 10px', fontSize: 11, marginTop: 4 }}>{(c.effect_options || []).map(o => <option key={o} value={o}>{o}</option>)}</select>}
                {!c.enabled && c.verdict !== 'pending' && <div style={{ ...muted, fontStyle: 'italic' }}>Off. Tick the box to turn it on; it is rendered and checked again.</div>}
                <div style={{ ...muted, marginTop: 2 }}>
                  <span style={{ ...tag, color: c.verdict === 'kept' ? '#0a0a0a' : '#737373', borderColor: c.verdict === 'kept' ? '#0a0a0a' : '#d4d4d4' }}>{c.verdict === 'kept' ? 'kept' : c.verdict === 'reverted' ? 'reverted' : c.verdict === 'off' ? 'off by default' : c.verdict}</span>
                  <span style={{ ...tag, marginLeft: 6 }}>{c.risk === 'safe' ? 'safe fix' : 'taste call'}</span>
                  {c.user_override && <span style={{ ...tag, marginLeft: 6 }}>your choice</span>}
                  <span style={{ marginLeft: 8 }}>{c.verdict_reasons.join(' · ')}</span>
                </div>
              </div>
            </li>
          ))}
        </ul>

        <h3 style={h3}>Style pack, fonts and sound</h3>
        {st.packs && <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }} data-testid="style-packs">
          {Object.entries(st.packs).map(([k, p]) => (
            <button key={k} disabled={busy || st.running} onClick={() => call('POST', '/style', { pack: k })} style={{ ...(st.style?.pack === k ? btn : btn2), textAlign: 'left', padding: '8px 14px' }} title={p.for}>
              <span style={{ display: 'inline-block', width: 9, height: 9, borderRadius: 9, background: `#${p.accent}`, border: '1px solid #d4d4d4', marginRight: 6 }} />{p.label}
              <div style={{ fontSize: 10, opacity: 0.7, fontWeight: 400 }}>{p.headline} + {p.caption}</div>
            </button>
          ))}
        </div>}
        {st.style_choice && <p style={{ ...muted, margin: '0 0 8px' }}>Suggested for this video: <b>{st.packs?.[st.style_choice.pack]?.label || st.style_choice.pack}</b>{st.style_choice.pack_reason ? ` (${st.style_choice.pack_reason})` : ''}. {st.style_choice.emphasis_count} words are emphasised.</p>}
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
          <FontPicker label="Headline font" value={st.style?.font_headline} busy={busy || st.running} auth={auth} onPick={f => call('POST', '/style', { font_headline: f })} />
          <FontPicker label="Caption font" value={st.style?.font_caption} busy={busy || st.running} auth={auth} onPick={f => call('POST', '/style', { font_caption: f })} />
        </div>
        {(st.style_choice?.fonts || []).length > 0 && <div style={{ marginBottom: 8 }}>
          <div style={{ ...muted, fontWeight: 600, marginBottom: 4 }}>Suggested fonts (one click):</div>
          {st.style_choice!.fonts.map((f, i) => <button key={i} disabled={busy || st.running} onClick={() => call('POST', '/style', f.role === 'headline' ? { font_headline: f.family } : { font_caption: f.family })} style={{ ...btn2, padding: '6px 12px', fontSize: 12, marginRight: 6, marginBottom: 6 }} title={f.reason}>{f.role}: {f.family}</button>)}
        </div>}
        {(st.style_notes || []).map((n, i) => <p key={i} style={{ ...muted, color: '#b45309', margin: '0 0 6px' }}>{n}</p>)}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 6 }} data-testid="intensity">
          <span style={{ ...muted, fontWeight: 600 }}>Effects:</span>
          {(st.intensities || []).map(k => <button key={k} disabled={busy || st.running} onClick={() => call('POST', '/style', { intensity: k })} style={{ ...(st.style?.intensity === k ? btn : btn2), padding: '6px 14px', fontSize: 12 }}>{k[0].toUpperCase() + k.slice(1)}</button>)}
          <label style={{ ...muted, display: 'flex', alignItems: 'center', gap: 6, marginLeft: 8 }}><input type="checkbox" checked={st.style?.sfx !== false} disabled={busy || st.running} onChange={e => call('POST', '/style', { sfx: e.target.checked })} style={{ width: 16, height: 16, accentColor: '#0a0a0a' }} />Sound effects</label>
        </div>
        <p style={{ ...muted, margin: '0 0 6px' }}>Sound effects are synthesised, mixed far under your voice and never replace your audio. No background music is added.</p>

        <h3 style={h3}>Style and requests</h3>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
          {(st.presets || []).map(p => <button key={p} disabled={busy || st.running} onClick={() => call('POST', '/preset', { preset: p })} style={p === st.preset ? btn : btn2}>{p[0].toUpperCase() + p.slice(1)}</button>)}
        </div>
        <form onSubmit={async e => { e.preventDefault(); if (text.trim() && await call('POST', '/revise', { text })) setText('') }} style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <input value={text} onChange={e => setText(e.target.value)} placeholder="e.g. bigger captions, less zoom, keep the intro" maxLength={300} aria-label="Ask for a change" style={{ flex: '1 1 220px', minWidth: 0, border: '1px solid #d4d4d4', borderRadius: 999, padding: '10px 16px', fontSize: 13 }} />
          <button type="submit" style={btn} disabled={busy || st.running || st.revisions_left <= 0}>Apply</button>
        </form>
        <div style={{ ...muted, marginTop: 6 }}>{st.revisions_left} of 5 requests left. {st.revisions.length > 0 && 'Done: ' + st.revisions.map(r => `“${r.text}”`).join(', ')}</div>

        <h3 style={h3}>Verification</h3>
        <p style={{ ...muted, margin: '0 0 8px' }}>Safe fixes are kept when the tool checks pass and the critic (an AI that watches both videos) finds no defect. Taste calls (zoom, beat-synced cuts, labels, sound effects) are kept only if the critic also approves. The AI score below is informational and never decides anything.{st.critic?.notes ? ` Critic: ${st.critic.notes}` : ''}{st.timing?.total_s ? ` Took ${Math.round(st.timing.total_s)} s.` : ''}</p>
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
            <p style={{ ...muted, margin: '0 0 6px' }}><span style={{ ...tag, marginRight: 6 }}>AI-estimated</span>Informational only, not measured viewership. This estimate is noisy (a few points up or down, sometimes more): a change in something the edit did not touch is noise.</p>
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
          <summary style={{ ...muted, cursor: 'pointer', fontWeight: 600 }}>Checks per change group</summary>
          {Object.entries(st.groups).map(([g, v]) => (
            <div key={g} style={{ margin: '10px 0' }}>
              <div style={{ fontSize: 12, fontWeight: 700 }}>{v.label} — {v.status} · {v.risk === 'safe' ? 'safe fix' : 'taste call'}{v.critic ? ` · critic: ${v.critic.verdict}` : ''}</div>
              <div style={muted}>{(v.reasons || []).join(' · ')}</div>
              {(v.critic?.defects || []).map((d, i) => <div key={i} style={{ ...muted, color: d.severity === 'major' ? '#b91c1c' : '#737373' }}>Critic ({d.severity}): {d.flag} {d.detail}</div>)}
              {(v.objective?.checks || []).map((c, i) => <div key={i} style={{ ...muted, color: c.ok ? '#525252' : c.warn ? '#737373' : '#b91c1c' }}>{c.ok ? '✓' : c.warn ? '!' : '✗'} {c.name}: {c.detail}</div>)}
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
