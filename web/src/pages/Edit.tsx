// Built during the Meta Global AI Developer Hackathon (new work).
// The Auto-edit studio, /edit/:analysisId (login required). Left, sticky: the Before and After players at the same 9:16 size and the
// download row. Right: a progress stepper with named stages while it runs (the raw log is a collapsed "Activity log"), then
// 1 Changes (Safe fixes / Taste calls, each with a toggle, a KEPT or REVERTED chip and a one-line reason), 2 Style, 3 Ask for changes,
// 4 Verified, 5 Do it yourself, and an "Export for Instagram Edits" block. There is no link to Instagram Edits: the files are moved by hand.
// Every request goes through authFetch. Black and white on the page's cream background; the layout is a CSS grid that stacks on a phone.
import { useEffect, useMemo, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import { isLoggedIn } from '@/lib/auth'
import { keptChanges, useAutoEdit, useBlobUrl, type Change, type Metrics, type State, type Tip } from '@/studio/autoedit/api'
import FontPicker from '@/studio/autoedit/FontPicker'
import { box, btn, btn2, fmt, h2, h3, muted, SCORED, tag } from '@/studio/autoedit/ui'

// ── the stages the stepper names, in the order the server runs them (its `phase` says which one is on) ──
const STAGES: { key: string; label: string; hint: string }[] = [
  { key: 'queued', label: 'Queued', hint: 'waiting for a free slot' },
  { key: 'inputs', label: 'Reading your video', hint: 'speech with word timings, cuts, pauses, loudness' },
  { key: 'planning', label: 'Planning the edit', hint: 'the planner and the style director work together' },
  { key: 'proxies', label: 'Rendering previews', hint: 'small test renders of each group of changes' },
  { key: 'verify', label: 'Checking every change', hint: 'tool checks, then a critic that watches both videos' },
  { key: 'finalising', label: 'Final render', hint: 'one full-resolution render of the kept changes' },
]
const stageIndex = (phase: string) => {
  const p = (phase || '').split(':')[0]
  if (p === 'rendering') return STAGES.length - 1          // a re-render after a toggle or a request
  const i = STAGES.findIndex(s => s.key === p)
  return i < 0 ? 0 : i
}

function useReportScores(analysisId?: string): Record<string, number> | null {
  return useMemo(() => {
    try {
      const raw = localStorage.getItem('prysm_result')
      if (!raw || !analysisId) return null
      const r = JSON.parse(raw)
      if (r.analysis_id !== analysisId) return null
      const out: Record<string, number> = {}
      for (const p of r.parameters || []) if (typeof p.score === 'number') out[p.name] = Math.round(p.score)
      return Object.keys(out).length ? out : null
    } catch { return null }
  }, [analysisId])
}

function Section({ n, title, sub, children, testid }: { n?: number; title: string; sub?: string; children: ReactNode; testid?: string }) {
  return (
    <section style={box} data-testid={testid}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        {n != null && <span style={{ ...tag, color: '#0a0a0a', borderColor: '#0a0a0a' }}>{n}</span>}
        <h2 style={h2}>{title}</h2>
      </div>
      {sub && <p style={{ ...muted, margin: '4px 0 0' }}>{sub}</p>}
      <div style={{ marginTop: 12 }}>{children}</div>
    </section>
  )
}

function Player({ title, path, version, waiting }: { title: string; path: string | null; version: number; waiting?: string }) {
  const { url, failed } = useBlobUrl(path, version)
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ ...muted, fontWeight: 700, marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.1em', fontSize: 10 }}>{title}</div>
      <div style={{ background: '#0a0a0a', borderRadius: 12, aspectRatio: '9 / 16', overflow: 'hidden', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {url ? <video src={url} controls playsInline style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
          : <div style={{ color: '#a3a3a3', fontSize: 12, padding: 12, textAlign: 'center' }}>{!path ? waiting || '' : failed ? 'Not available' : 'Loading…'}</div>}
      </div>
    </div>
  )
}

function Switch({ checked, disabled, onChange, label }: { checked: boolean; disabled: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)}
      style={{ all: 'unset', cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.5 : 1, flex: 'none', width: 38, height: 22, borderRadius: 999, background: checked ? '#0a0a0a' : '#d4d4d4', position: 'relative', transition: 'background .15s' }}>
      <span style={{ position: 'absolute', top: 2, left: checked ? 18 : 2, width: 18, height: 18, borderRadius: 18, background: '#fff', transition: 'left .15s' }} />
    </button>
  )
}

const chip = (kind: 'kept' | 'reverted' | 'off' | 'other'): CSSProperties =>
  kind === 'kept' ? { ...tag, background: '#0a0a0a', color: '#fff', borderColor: '#0a0a0a' }
    : kind === 'reverted' ? { ...tag, color: '#b91c1c', borderColor: '#b91c1c' } : { ...tag }

function ChangeRow({ c, busy, running, onToggle, onSwap }: { c: Change; busy: boolean; running: boolean; onToggle: (v: boolean) => void; onSwap: (effect: string) => void }) {
  const kind = c.verdict === 'kept' ? 'kept' : c.verdict === 'reverted' ? 'reverted' : c.verdict === 'off' ? 'off' : 'other'
  const why = c.verdict_reasons[0] || c.reason
  return (
    <li style={{ display: 'flex', gap: 12, alignItems: 'flex-start', padding: '10px 0', borderTop: '1px solid #f0f0f0' }} data-testid="change-row">
      <div style={{ paddingTop: 1 }}><Switch checked={c.enabled} disabled={busy || running} onChange={onToggle} label={`Toggle: ${c.label}`} /></div>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13, fontWeight: 600, wordBreak: 'break-word' }}>{c.label}</span>
          <span style={chip(kind)} data-testid="verdict-chip">{kind === 'kept' ? 'KEPT' : kind === 'reverted' ? 'REVERTED' : kind === 'off' ? 'OFF' : c.verdict}</span>
          {c.user_override && <span style={tag}>your choice</span>}
        </div>
        <div style={{ ...muted, marginTop: 2 }} title={[c.reason, c.target_parameter, c.finding ? `Answers: ${c.finding}` : ''].filter(Boolean).join(' · ')}>{why}</div>
        {(c.effect_options || []).length > 1 && (
          <select value={(c.label.match(/\((\w+),/) || [])[1] || ''} disabled={busy || running} onChange={e => onSwap(e.target.value)} aria-label="Swap sound effect"
            style={{ ...btn2, padding: '3px 10px', fontSize: 11, marginTop: 4 }}>{(c.effect_options || []).map(o => <option key={o} value={o}>{o}</option>)}</select>
        )}
      </div>
    </li>
  )
}

function Stepper({ st }: { st: State }) {
  const cur = stageIndex(st.phase)
  return (
    <div data-testid="stepper">
      {!st.running && <p style={{ ...muted, margin: 0 }} data-testid="stepper-done">All {STAGES.length - 1} stages finished: reading, planning, previews, checks and the final render.</p>}
      {st.running && <ol style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {STAGES.map((s, i) => {
          const state = st.running ? (i < cur ? 'done' : i === cur ? 'now' : 'todo') : 'done'
          return (
            <li key={s.key} style={{ display: 'flex', gap: 12, alignItems: 'flex-start', padding: '7px 0' }} data-state={state}>
              <span aria-hidden="true" style={{ flex: 'none', width: 20, height: 20, borderRadius: 20, marginTop: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 700,
                background: state === 'done' ? '#0a0a0a' : '#fff', color: state === 'done' ? '#fff' : '#0a0a0a', border: `1.5px solid ${state === 'todo' ? '#d4d4d4' : '#0a0a0a'}` }}>
                {state === 'done' ? '✓' : state === 'now' ? <span className="animate-pulse" style={{ width: 8, height: 8, borderRadius: 8, background: '#0a0a0a' }} /> : ''}
              </span>
              <div>
                <div style={{ fontSize: 13, fontWeight: state === 'now' ? 700 : 500, color: state === 'todo' ? '#a3a3a3' : '#0a0a0a' }}>{s.label}</div>
                {state === 'now' && <div style={muted}>{s.hint}</div>}
              </div>
            </li>
          )
        })}
      </ol>}
      <details style={{ marginTop: 8 }} data-testid="activity-log">
        <summary style={{ ...muted, cursor: 'pointer', fontWeight: 600 }}>Activity log</summary>
        <ol style={{ margin: '6px 0 0', padding: 0, listStyle: 'none', maxHeight: 220, overflowY: 'auto' }} data-testid="auto-edit-trace">
          {st.trace.slice(-40).map((t, i) => (
            <li key={i} style={{ fontSize: 12, lineHeight: 1.5, color: '#525252', display: 'flex', gap: 8 }}>
              <span style={{ width: 14, flex: 'none' }}>{t.kind === 'kept' ? '✓' : t.kind === 'reverted' ? '↩' : '·'}</span><span>{t.msg}</span>
            </li>
          ))}
        </ol>
      </details>
    </div>
  )
}

const TH: CSSProperties = { padding: '6px 4px', fontWeight: 600 }

function MeasuredTable({ m0, m1, pausesRemoved }: { m0?: Metrics; m1?: Metrics; pausesRemoved: number }) {
  const rows: [string, string, string][] = [
    ['Length (s)', fmt(m0?.duration_s), fmt(m1?.duration_s)],
    ['Loudness (LUFS)', fmt(m0?.lufs), fmt(m1?.lufs)],
    ['True peak (dBTP)', fmt(m0?.true_peak_db), fmt(m1?.true_peak_db)],
    ['Silence share', fmt(m0?.silence_ratio, 2), fmt(m1?.silence_ratio, 2)],
    ['Pauses removed', '0', String(pausesRemoved)],
    ['Hard cuts (shot changes)', fmt(m0?.cut_count, 0), fmt(m1?.cut_count, 0)],
    ['Average shot (s)', fmt(m0?.avg_shot_s), fmt(m1?.avg_shot_s)],
  ]
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }} data-testid="measured-table">
        <thead><tr style={{ textAlign: 'left', color: '#737373' }}><th style={TH}>Measured by tools</th><th style={TH}>Before</th><th style={TH}>After</th></tr></thead>
        <tbody>{rows.map(([l, b, a]) => <tr key={l} style={{ borderTop: '1px solid #f0f0f0' }}><td style={{ padding: '6px 4px' }}>{l}</td><td>{b}</td><td>{a}</td></tr>)}</tbody>
      </table>
      <p style={{ ...muted, margin: '6px 0 0' }}>&ldquo;Pauses removed&rdquo; counts the silent gaps the edit cut out. &ldquo;Hard cuts&rdquo; are shot changes in the picture, so pausing cuts do not raise it.</p>
    </div>
  )
}

/** The AI re-score never contradicts the report: "before" is the report's own score, "after" is that score plus the change the AI estimated. */
function RescoreTable({ before, after, report }: { before: Record<string, number>; after: Record<string, number>; report: Record<string, number> | null }) {
  if (!report) return null
  const rows = SCORED.filter(k => k in before && k in after && k in report).map(k => {
    const d = Math.round(after[k] - before[k])
    return { k, b: report[k], a: Math.max(0, Math.min(100, report[k] + d)), d }
  })
  if (!rows.length) return null
  return (
    <div style={{ overflowX: 'auto', marginTop: 14 }} data-testid="rescore">
      <p style={{ ...muted, margin: '0 0 6px' }}><span style={{ ...tag, marginRight: 6 }}>AI-estimated, informational</span>Only the change comes from the AI re-score; the &ldquo;before&rdquo; values are your report&rsquo;s own scores. This estimate is noisy (a few points either way, sometimes more), so a change in something the edit did not touch is noise.</p>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead><tr style={{ textAlign: 'left', color: '#737373' }}><th style={TH}>Parameter (AI-estimated)</th><th style={TH}>Report</th><th style={TH}>After edit</th><th style={TH}>Change</th></tr></thead>
        <tbody>{rows.map(r => <tr key={r.k} style={{ borderTop: '1px solid #f0f0f0' }}><td style={{ padding: '6px 4px' }}>{r.k}</td><td>{r.b}</td><td>{r.a}</td><td style={{ fontWeight: r.d ? 700 : 400 }}>{r.d > 0 ? '+' : ''}{r.d}</td></tr>)}</tbody>
      </table>
    </div>
  )
}

function ManualTips({ tips }: { tips: Tip[] }) {
  const li = (t: Tip, i: number) => <li key={i} style={{ fontSize: 13, lineHeight: 1.5, color: '#404040', marginBottom: 6 }}>{t.tip}{t.why_manual ? <span style={{ color: '#a3a3a3' }}> ({t.why_manual})</span> : null}</li>
  return (
    <>
      <ul style={{ margin: 0, paddingLeft: 18, listStyle: 'disc' }}>{tips.slice(0, 5).map(li)}</ul>
      {tips.length > 5 && (
        <details style={{ marginTop: 4 }} data-testid="tips-more">
          <summary style={{ ...btn2, display: 'inline-block', padding: '5px 14px', fontSize: 12, cursor: 'pointer', listStyle: 'none' }}>{tips.length - 5} more</summary>
          <ul style={{ margin: '10px 0 0', paddingLeft: 18, listStyle: 'disc' }}>{tips.slice(5).map((t, i) => li(t, i + 5))}</ul>
        </details>
      )}
    </>
  )
}

export default function Edit() {
  const { analysisId } = useParams()
  if (!isLoggedIn()) return <Navigate to="/login" replace />
  return <EditStudio analysisId={analysisId} />
}

function EditStudio({ analysisId }: { analysisId?: string }) {
  const ae = useAutoEdit(analysisId)
  const { data, enabled, busy, err, version, base, call, load, download, setData } = ae
  const [text, setText] = useState('')
  const [preset, setPreset] = useState('clean')
  const report = useReportScores(analysisId)
  const backToResults = (() => { try { return JSON.parse(localStorage.getItem('prysm_result') || '{}').analysis_id === analysisId } catch { return false } })()
  useEffect(() => { if (data?.state) setPreset(data.state.preset) }, [data?.state?.preset]) // eslint-disable-line react-hooks/exhaustive-deps
  const st = data?.state || null

  const nav = (
    <nav className="border-b border-border px-3 sm:px-8 h-14 flex items-center justify-between sticky top-0 bg-background/90 backdrop-blur-md z-50">
      <a href="/" className="font-logo text-[18px] sm:text-[21px] text-foreground">PRYSM</a>
      <div className="flex items-center gap-3 sm:gap-4">
        <a href="/dashboard" className="text-xs sm:text-sm text-foreground/30 hover:text-foreground/50 transition-colors">Dashboard</a>
        <a href="/analyse" className="text-xs sm:text-sm text-foreground/40 hover:text-foreground/60 transition-colors">New Analysis →</a>
      </div>
    </nav>
  )
  const back = (
    <Link to={backToResults ? '/results' : '/dashboard'} className="text-[13px] text-foreground/50 hover:text-foreground transition-colors font-body" data-testid="back-link">
      ← {backToResults ? 'Back to results' : 'Back to dashboard'}
    </Link>
  )
  const shell = (body: ReactNode) => (
    <div className="min-h-screen bg-background text-foreground">
      {nav}
      <div className="max-w-7xl mx-auto px-3 sm:px-6 py-4 sm:py-6">{back}<div style={{ height: 12 }} />{body}</div>
    </div>
  )

  if (enabled === null || (enabled && !data)) return shell(<p className="text-foreground/40 font-body text-sm">Loading…</p>)
  if (!enabled) return shell(<div style={box}><h1 style={h2}>Auto-edit is not available</h1><p style={{ ...muted, margin: '6px 0 0' }}>It is switched off on this server, or you need to sign in again.</p></div>)
  if (!data) return null

  const done = !!st && !st.running && !!st.final
  const kept = keptChanges(st)
  const pausesRemoved = kept.filter(c => c.type === 'cut_segment' && c.id.startsWith('auto_silence')).length
  const safe = (st?.changes || []).filter(c => c.risk === 'safe')
  const taste = (st?.changes || []).filter(c => c.risk !== 'safe')
  const toggle = (c: Change) => (v: boolean) => call('POST', '/toggle', { op_id: c.id, enabled: v })
  const swap = (c: Change) => (effect: string) => call('POST', '/swap', { op_id: c.id, effect })
  const overall = st?.final?.overall_scores

  const left = (
    <div className="lg:sticky lg:top-[72px] self-start" data-testid="edit-left">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12 }}>
        <Player title="Before (your upload)" path={data.eligible || st ? `${base}/download/original` : null} version={0} waiting="Not available" />
        <Player title="After" path={done ? `${base}/download/mp4` : null} version={version} waiting={st?.running ? 'Working on it…' : 'Your edit appears here'} />
      </div>
      {done && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }} data-testid="downloads">
          <button style={btn} onClick={() => download('mp4', 'prysm-edit.mp4')}>Download MP4</button>
          {st?.final?.has_captions && <><button style={btn2} onClick={() => download('srt', 'captions.srt')}>Captions (SRT)</button><button style={btn2} onClick={() => download('ass', 'captions.ass')}>Captions (ASS)</button></>}
          <button style={btn2} onClick={() => download('cover', 'cover.jpg')}>Cover image</button>
          <button style={btn2} onClick={() => download('edl', 'edit-list.json')}>Edit list (JSON)</button>
        </div>
      )}
    </div>
  )

  const head = (
    <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'space-between', marginBottom: 12 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 800, margin: 0 }}>Auto-edit</h1>
        {st && <span style={tag}>{st.running ? 'working' : st.status === 'done' ? 'done' : st.status}</span>}
        {st?.cost && <span style={{ ...muted, fontSize: 11 }}>AI cost so far ${st.cost.total_usd.toFixed(2)}</span>}
      </div>
      {st && !st.running && (
        <div style={{ display: 'flex', gap: 8 }}>
          <button style={btn2} disabled={busy} onClick={async () => { if (await call('POST', '/start', { preset: st.preset })) load() }}>Run again</button>
          <button style={btn2} disabled={busy} onClick={async () => { if (window.confirm('Delete the edited video and the stored copy of your upload?') && await call('DELETE', '')) setData(d => d && { ...d, state: null, eligible: false, reason: 'Deleted. The stored files for this video were removed.' }) }}>Delete</button>
        </div>
      )}
    </div>
  )

  let right: ReactNode
  if (!st) {
    right = (
      <div style={box} data-testid="edit-start">
        <h2 style={h2}>Auto-edit this reel</h2>
        <p style={{ ...muted, margin: '6px 0 14px' }}>Prysm applies its own fixes to your video: tighter pacing, captions, hook text, zoom punches and loudness. Each change is checked and automatically undone if it does not help. Your original is never modified.</p>
        {data.eligible ? (
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
            <select value={preset} onChange={e => setPreset(e.target.value)} style={{ ...btn2, padding: '9px 14px' }} aria-label="Style preset">
              {['clean', 'energetic', 'minimal'].map(p => <option key={p} value={p}>{p[0].toUpperCase() + p.slice(1)}</option>)}
            </select>
            <button style={{ ...btn, opacity: busy ? 0.5 : 1 }} disabled={busy} onClick={async () => { if (await call('POST', '/start', { preset })) load() }}>Start Auto-edit</button>
          </div>
        ) : <p style={{ ...muted, margin: 0 }}>{data.reason}</p>}
        {err && <p style={{ ...muted, color: '#b91c1c' }}>{err}</p>}
      </div>
    )
  } else {
    right = (
      <>
        {head}
        {st.error && <p style={{ ...muted, color: '#b91c1c', margin: '0 0 12px' }}>{st.status === 'stopped' ? st.error + ' The changes verified so far are kept.' : st.error}</p>}
        {err && <p style={{ ...muted, color: '#b91c1c', margin: '0 0 12px' }}>{err}</p>}

        <section style={box}>
          <h2 style={h2}>{st.running ? 'Working on your edit' : 'What happened'}</h2>
          <div style={{ marginTop: 10 }}><Stepper st={st} /></div>
          {st.timing?.total_s ? <p style={{ ...muted, margin: '8px 0 0' }}>Took {Math.round(st.timing.total_s)} s.</p> : null}
        </section>

        {done && <>
          <Section n={1} title="Changes" sub="A safe fix stays when the tool checks pass and the critic finds no defect. A taste call also needs the critic's approval. Turn any change on or off; it is rendered and checked again." testid="changes">
            {st.changes.length === 0 && <p style={muted}>The plan had no changes that Prysm can make automatically. See &ldquo;Do it yourself&rdquo; below.</p>}
            {safe.length > 0 && <>
              <div style={{ ...h3, margin: '4px 0 2px' }}>Safe fixes</div>
              <ul style={{ listStyle: 'none', margin: 0, padding: 0 }} data-testid="safe-fixes">{safe.map(c => <ChangeRow key={c.id} c={c} busy={busy} running={st.running} onToggle={toggle(c)} onSwap={swap(c)} />)}</ul>
            </>}
            {taste.length > 0 && <>
              <div style={{ ...h3, margin: '14px 0 2px' }}>Taste calls</div>
              <ul style={{ listStyle: 'none', margin: 0, padding: 0 }} data-testid="taste-calls">{taste.map(c => <ChangeRow key={c.id} c={c} busy={busy} running={st.running} onToggle={toggle(c)} onSwap={swap(c)} />)}</ul>
            </>}
          </Section>

          <Section n={2} title="Style" sub="A pack sets the headline font, the caption font, the accent colour and the caption animation together." testid="style">
            {st.packs && <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }} data-testid="style-packs">
              {Object.entries(st.packs).map(([k, p]) => (
                <button key={k} disabled={busy || st.running} onClick={() => call('POST', '/style', { pack: k })} style={{ ...(st.style?.pack === k ? btn : btn2), textAlign: 'left', padding: '8px 14px' }} title={p.for}>
                  <span style={{ display: 'inline-block', width: 9, height: 9, borderRadius: 9, background: `#${p.accent}`, border: '1px solid #d4d4d4', marginRight: 6 }} />{p.label}
                  <div style={{ fontSize: 10, opacity: 0.7, fontWeight: 400 }}>{p.headline} + {p.caption}</div>
                </button>
              ))}
            </div>}
            {st.style_choice && <p style={{ ...muted, margin: '0 0 8px' }} data-testid="pack-note">
              {st.style?.pack && st.style.pack !== st.style_choice.pack
                ? <>Rendered with <b>{st.packs?.[st.style.pack]?.label || st.style.pack}</b> ({st.style.pack_source === 'your choice' ? 'your choice' : 'set by a request'}). Prysm suggested <b>{st.packs?.[st.style_choice.pack]?.label || st.style_choice.pack}</b>.</>
                : <>Suggested and used: <b>{st.packs?.[st.style_choice.pack]?.label || st.style_choice.pack}</b>{st.style_choice.pack_reason ? ` (${st.style_choice.pack_reason})` : ''}.</>} {st.style_choice.emphasis_count} words are emphasised.
            </p>}
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
              <FontPicker label="Headline font" value={st.style?.font_headline} busy={busy || st.running} onPick={f => call('POST', '/style', { font_headline: f })} />
              <FontPicker label="Caption font" value={st.style?.font_caption} busy={busy || st.running} onPick={f => call('POST', '/style', { font_caption: f })} />
            </div>
            {(st.style_choice?.fonts || []).length > 0 && <div style={{ marginBottom: 8 }}>
              <div style={{ ...muted, fontWeight: 600, marginBottom: 4 }}>Other fonts that could suit this video (one click):</div>
              {st.style_choice!.fonts.map((f, i) => <button key={i} disabled={busy || st.running} onClick={() => call('POST', '/style', f.role === 'headline' ? { font_headline: f.family } : { font_caption: f.family })} style={{ ...btn2, padding: '6px 12px', fontSize: 12, marginRight: 6, marginBottom: 6 }} title={f.reason}>{f.role}: {f.family}</button>)}
            </div>}
            {(st.style_notes || []).map((n, i) => <p key={i} style={{ ...muted, color: '#b45309', margin: '0 0 6px' }}>{n}</p>)}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 6 }} data-testid="intensity">
              <span style={{ ...muted, fontWeight: 600 }}>Effects:</span>
              {(st.intensities || []).map(k => <button key={k} disabled={busy || st.running} onClick={() => call('POST', '/style', { intensity: k })} style={{ ...(st.style?.intensity === k ? btn : btn2), padding: '6px 14px', fontSize: 12 }}>{k[0].toUpperCase() + k.slice(1)}</button>)}
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 6 }}>
              <span style={{ ...muted, fontWeight: 600 }}>Look:</span>
              {(st.presets || []).map(p => <button key={p} disabled={busy || st.running} onClick={() => call('POST', '/preset', { preset: p })} style={{ ...(p === st.preset ? btn : btn2), padding: '6px 14px', fontSize: 12 }}>{p[0].toUpperCase() + p.slice(1)}</button>)}
              <span style={{ ...muted, fontWeight: 600, marginLeft: 8 }}>Sound:</span>
              <label style={{ ...muted, display: 'flex', alignItems: 'center', gap: 6 }}><input type="checkbox" checked={st.style?.sfx !== false} disabled={busy || st.running} onChange={e => call('POST', '/style', { sfx: e.target.checked })} style={{ width: 16, height: 16, accentColor: '#0a0a0a' }} />Sound effects</label>
            </div>
            <p style={{ ...muted, margin: 0 }}>Sound effects are synthesised, mixed far under your voice and never replace your audio. No background music is added.</p>
          </Section>

          <Section n={3} title="Ask for changes" sub="Describe a change in your own words; it is applied, rendered and checked." testid="ask">
            <form onSubmit={async e => { e.preventDefault(); if (text.trim() && await call('POST', '/revise', { text })) setText('') }} style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <input value={text} onChange={e => setText(e.target.value)} placeholder="e.g. bigger captions, less zoom, keep the intro" maxLength={300} aria-label="Ask for a change" style={{ flex: '1 1 220px', minWidth: 0, border: '1px solid #d4d4d4', borderRadius: 999, padding: '10px 16px', fontSize: 13 }} />
              <button type="submit" style={btn} disabled={busy || st.running || st.revisions_left <= 0}>Apply</button>
            </form>
            <div style={{ ...muted, marginTop: 6 }} data-testid="requests-left">{st.revisions_left} of 5 requests left. {st.revisions.length > 0 && 'Done: ' + st.revisions.map(r => `“${r.text}”`).join(', ')}</div>
          </Section>

          <Section n={4} title="Verified" sub={`Measured by tools on the real files.${st.critic?.notes ? ` Critic: ${st.critic.notes}` : ''}`} testid="verified">
            {st.final?.not_rescored && <p style={{ ...muted, margin: '0 0 8px' }}>The latest change was re-checked by tools only; the AI re-score below is from the original run.</p>}
            <MeasuredTable m0={st.baseline?.metrics} m1={st.final?.metrics} pausesRemoved={pausesRemoved} />
            {overall && <RescoreTable before={overall.before} after={overall.after} report={report} />}
            <details style={{ marginTop: 12 }} data-testid="group-checks">
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
          </Section>

          {st.manual_tips.length > 0 && (
            <Section n={5} title="Do it yourself" sub="Fixes Prysm cannot make for you." testid="manual">
              <ManualTips tips={st.manual_tips} />
            </Section>
          )}

          <Section title="Export for Instagram Edits" sub="Prysm is not connected to Instagram Edits. You move the files by hand." testid="export">
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
              <button style={btn} onClick={() => download('mp4', 'prysm-edit.mp4')}>Download MP4</button>
              {st.final?.has_captions && <><button style={btn2} onClick={() => download('srt', 'captions.srt')}>Captions SRT</button><button style={btn2} onClick={() => download('ass', 'captions.ass')}>Captions ASS</button></>}
              <button style={btn2} onClick={() => download('edl', 'edit-list.json')}>Edit list JSON</button>
            </div>
            <ol style={{ margin: 0, paddingLeft: 20, listStyle: 'decimal' }} data-testid="export-steps">
              <li style={{ fontSize: 13, lineHeight: 1.5, marginBottom: 6 }}>Download the MP4 and get it onto your phone (AirDrop, a cloud drive or an email to yourself).</li>
              <li style={{ fontSize: 13, lineHeight: 1.5, marginBottom: 6 }}>In the Instagram Edits app, start a new project and add that MP4 from your camera roll. The captions are already part of the video.</li>
              <li style={{ fontSize: 13, lineHeight: 1.5 }}>To change captions or cuts there, use the SRT, the ASS and the edit list as a reference to retype lines and re-place cuts. Edits is not known to import them.</li>
            </ol>
          </Section>
        </>}
        {!done && st.manual_tips.length > 0 && !st.running && (
          <Section n={5} title="Do it yourself" testid="manual"><ManualTips tips={st.manual_tips} /></Section>
        )}
      </>
    )
  }

  return shell(
    <div className="grid gap-6 lg:gap-8 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]" data-testid="edit-layout">
      {left}
      <div style={{ minWidth: 0 }}>{right}</div>
    </div>,
  )
}

