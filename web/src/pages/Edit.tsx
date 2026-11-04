// Built during the Meta Global AI Developer Hackathon (new work).
// The Auto-edit studio, /edit/:analysisId (login required).
// Top: a summary of four before -> after tiles (length, loudness, silence share, pauses removed) plus the AI cost.
// Left, sticky: the Before and After players (each at least 260 px wide on a desktop), one "Play both" button with synced play, pause and
// scrub, and the download row. Right: a stage stepper with animated progress while it runs, then Changes (grouped into about six groups,
// one toggle each, a KEPT / REVERTED chip, items with exact timestamps and the checks), Style, Ask for changes, Verified (tiles, the full
// table behind Details), Do it yourself (collapsed, with a count) and "Finish in Instagram Edits": a clean cut (only the safe pause and
// loudness fixes), a recipe of exact timestamps for the overlays, and Send to phone. There is no link to Instagram Edits and the page
// says so. Every request goes through authFetch. One card surface (.p-card), 12 px minimum for notes, reduced-motion safe.
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import QRCode from 'qrcode'
import { apiUrl } from '@/lib/api'
import { authFetch, isLoggedIn } from '@/lib/auth'
import { useCountUp } from '@/lib/motion'
import { keptChanges, pausesRemoved, useAutoEdit, useBlobUrl, type Change, type Metrics, type State, type Tip } from '@/studio/autoedit/api'
import FontPicker from '@/studio/autoedit/FontPicker'
import { groupChanges, groupVerdict, recipeLines, whenText, type ChangeGroup } from '@/studio/autoedit/groups'
import { btn, btn2, fmt, h2, h3, INK, INK2, LINE, muted, SCORED, tag } from '@/studio/autoedit/ui'

// ── the stages the stepper names, in the order the server runs them (its `phase` says which one is on) ──
const STAGES: { key: string; label: string; hint: string }[] = [
  { key: 'inputs', label: 'Reading your video', hint: 'speech with word timings, cuts, pauses, loudness' },
  { key: 'planning', label: 'Planning', hint: 'the planner and the style director work together' },
  { key: 'proxies', label: 'Previews', hint: 'small test renders of each group of changes' },
  { key: 'verify', label: 'Checks', hint: 'tool checks, then a critic that watches both videos' },
  { key: 'finalising', label: 'Final render', hint: 'one full-resolution render of the kept changes' },
]
const stageIndex = (phase: string) => {
  const p = (phase || '').split(':')[0]
  if (p === 'queued') return -1
  if (p === 'rendering') return STAGES.length - 1          // a re-render after a toggle, a request or a clean cut
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

function Section({ n, title, sub, children, testid, collapsible, count }: { n?: number; title: string; sub?: string; children: ReactNode; testid?: string; collapsible?: boolean; count?: number }) {
  const head = (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
      {n != null && <span style={{ ...tag, color: INK, borderColor: INK }}>{n}</span>}
      <h2 style={h2}>{title}</h2>
      {count != null && <span style={tag} data-testid="section-count">{count}</span>}
    </div>
  )
  if (collapsible) {
    return (
      <details className="p-card p-fade" style={{ padding: 20, marginBottom: 16 }} data-testid={testid}>
        <summary style={{ cursor: 'pointer', listStyle: 'none' }}>{head}{sub && <p style={{ ...muted, margin: '4px 0 0' }}>{sub}</p>}</summary>
        <div style={{ marginTop: 12 }}>{children}</div>
      </details>
    )
  }
  return (
    <section className="p-card p-fade" style={{ padding: 20, marginBottom: 16 }} data-testid={testid}>
      {head}
      {sub && <p style={{ ...muted, margin: '4px 0 0' }}>{sub}</p>}
      <div style={{ marginTop: 12 }}>{children}</div>
    </section>
  )
}

// ── tiles ─────────────────────────────────────────────────────────────────────────────────────
function Tile({ label, before, after, unit, d = 1, good, testid }: { label: string; before: number | null | undefined; after: number | null | undefined; unit: string; d?: number; good?: 'down' | 'up'; testid: string }) {
  const shown = useCountUp(after ?? null)
  const delta = before != null && after != null ? after - before : null
  const improved = delta != null && delta !== 0 && good ? (good === 'down' ? delta < 0 : delta > 0) : null
  return (
    <div className="p-card" style={{ padding: '12px 14px', minWidth: 0 }} data-testid={testid}>
      <div style={{ fontSize: 12, color: INK2 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 800, lineHeight: 1.2, marginTop: 2 }}>{shown == null ? '–' : shown.toFixed(d)}<span style={{ fontSize: 12, fontWeight: 500, color: INK2 }}> {unit}</span></div>
      <div style={{ fontSize: 12, color: INK2, marginTop: 2 }} data-testid={`${testid}-delta`}>
        {before == null ? 'before: –' : `was ${before.toFixed(d)}`}
        {delta != null && <span style={{ marginLeft: 8, fontWeight: 700, color: improved === false ? '#8a5a12' : INK }}>{delta > 0 ? '+' : delta < 0 ? '−' : ''}{Math.abs(delta).toFixed(d)}</span>}
      </div>
    </div>
  )
}

function Summary({ st }: { st: State }) {
  const m0 = st.baseline?.metrics, m1 = st.final?.metrics
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10, marginBottom: 16 }} data-testid="summary">
      <Tile testid="t-length" label="Length" before={m0?.duration_s} after={m1?.duration_s} unit="s" good="down" />
      <Tile testid="t-loud" label="Loudness" before={m0?.lufs} after={m1?.lufs} unit="LUFS" />
      <Tile testid="t-silence" label="Silence share" before={m0?.silence_ratio} after={m1?.silence_ratio} unit="" d={2} good="down" />
      <Tile testid="t-pauses" label="Pauses removed" before={0} after={pausesRemoved(st)} unit="" d={0} good="up" />
    </div>
  )
}

// ── players with a synced "Play both" ───────────────────────────────────────────────────────────
function Player({ title, path, version, waiting, onVideo }: { title: string; path: string | null; version: number; waiting?: string; onVideo: (el: HTMLVideoElement | null) => void }) {
  const { url, failed } = useBlobUrl(path, version)
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ ...muted, fontWeight: 700, marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.1em' }}>{title}</div>
      <div style={{ background: '#161616', borderRadius: 12, aspectRatio: '9 / 16', overflow: 'hidden', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {url ? <video ref={onVideo} src={url} controls playsInline style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
          : <div style={{ color: '#d4d4d4', fontSize: 12, padding: 12, textAlign: 'center' }}>{!path ? waiting || '' : failed ? 'Not available' : 'Loading…'}</div>}
      </div>
    </div>
  )
}

/** Two videos that play, pause and scrub together (by time; the edit is shorter, so the second one stops at its own end). */
function useSyncedPair() {
  const [a, setA] = useState<HTMLVideoElement | null>(null)
  const [b, setB] = useState<HTMLVideoElement | null>(null)
  const [playing, setPlaying] = useState(false)
  useEffect(() => {
    if (!a || !b) return
    const link = (v: HTMLVideoElement, o: HTMLVideoElement) => {
      const onPlay = () => { setPlaying(true); if (o.paused && !o.ended) void o.play().catch(() => {}) }
      const onPause = () => { if (!o.paused) o.pause(); setPlaying(false) }
      const onSeek = () => { const t = Math.min(v.currentTime, Math.max(0, (o.duration || v.currentTime) - 0.05)); if (Math.abs(o.currentTime - t) > 0.25) o.currentTime = t }
      v.addEventListener('play', onPlay); v.addEventListener('pause', onPause); v.addEventListener('seeked', onSeek)
      return () => { v.removeEventListener('play', onPlay); v.removeEventListener('pause', onPause); v.removeEventListener('seeked', onSeek) }
    }
    const u1 = link(a, b), u2 = link(b, a)
    return () => { u1(); u2() }
  }, [a, b])
  const toggle = useCallback(() => {
    if (!a || !b) return
    if (a.paused) { b.currentTime = Math.min(a.currentTime, b.duration || a.currentTime); void a.play().catch(() => {}); void b.play().catch(() => {}) } else { a.pause(); b.pause() }
  }, [a, b])
  return { setA, setB, playing, toggle, ready: !!a && !!b }
}

// ── changes ─────────────────────────────────────────────────────────────────────────────────────
function Switch({ checked, disabled, onChange, label }: { checked: boolean; disabled: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)}
      style={{ all: 'unset', cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.5 : 1, flex: 'none', width: 38, height: 22, borderRadius: 999, background: checked ? INK : '#cfc9b9', position: 'relative', transition: 'background .15s' }}>
      <span style={{ position: 'absolute', top: 2, left: checked ? 18 : 2, width: 18, height: 18, borderRadius: 18, background: '#fffdf6', transition: 'left .15s' }} />
    </button>
  )
}

const chip = (v: string): CSSProperties =>
  v === 'KEPT' ? { ...tag, background: INK, color: '#fff', borderColor: INK }
    : v === 'REVERTED' ? { ...tag, color: '#9b1c1c', borderColor: '#9b1c1c' } : { ...tag }

function ItemRow({ c, busy, running, onToggle, onSwap }: { c: Change; busy: boolean; running: boolean; onToggle: (v: boolean) => void; onSwap: (effect: string) => void }) {
  const v = c.verdict === 'kept' ? 'KEPT' : c.verdict === 'reverted' ? 'REVERTED' : c.verdict === 'off' ? 'OFF' : c.verdict.toUpperCase()
  const when = whenText(c)
  return (
    <li style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '9px 0', borderTop: `1px solid ${LINE}` }} data-testid="change-row">
      <Switch checked={c.enabled} disabled={busy || running} onChange={onToggle} label={`Toggle: ${c.label}`} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13, fontWeight: 600, wordBreak: 'break-word' }}>{c.label}</span>
          <span style={chip(v)} data-testid="verdict-chip">{v}</span>
          {c.user_override && <span style={tag}>your choice</span>}
        </div>
        {when && <div style={{ ...muted, color: INK }} data-testid="change-when">{when}</div>}
        <div style={muted}>{c.verdict_reasons[0] || c.reason}</div>
        {(c.effect_options || []).length > 1 && (
          <select value={(c.label.match(/\((\w+),/) || [])[1] || ''} disabled={busy || running} onChange={e => onSwap(e.target.value)} aria-label="Swap sound effect"
            style={{ ...btn2, padding: '3px 10px', fontSize: 12, marginTop: 4 }}>{(c.effect_options || []).map(o => <option key={o} value={o}>{o}</option>)}</select>
        )}
      </div>
    </li>
  )
}

function GroupCard({ g, st, busy, onGroup, onItem, onSwap }: { g: ChangeGroup; st: State; busy: boolean; onGroup: (ids: string[], v: boolean) => void; onItem: (c: Change, v: boolean) => void; onSwap: (c: Change, e: string) => void }) {
  const v = groupVerdict(g)
  const backend = Array.from(new Set(g.items.map(c => c.group))).map(k => st.groups[k]).filter(Boolean)
  const allOn = g.enabled === g.items.length
  return (
    <details style={{ borderTop: `1px solid ${LINE}` }} data-testid="change-group" data-key={g.key}>
      <summary style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 0', cursor: 'pointer', listStyle: 'none' }}>
        <span onClick={e => e.stopPropagation()}><Switch checked={allOn} disabled={busy || st.running} onChange={val => onGroup(g.items.filter(c => c.enabled !== val).map(c => c.id), val)} label={`Toggle all: ${g.label}`} /></span>
        <span style={{ fontSize: 14, fontWeight: 700, flex: 1, minWidth: 0 }}>{g.label} <span style={{ fontWeight: 500, color: INK2 }}>({g.items.length})</span></span>
        <span style={tag}>{g.risk === 'safe' ? 'safe fix' : 'taste call'}</span>
        <span style={chip(v)} data-testid="group-chip">{v}</span>
        <span aria-hidden="true" style={{ color: INK2, fontSize: 12 }}>▾</span>
      </summary>
      <div style={{ paddingLeft: 4 }}>
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {g.items.map(c => <ItemRow key={c.id} c={c} busy={busy} running={st.running} onToggle={val => onItem(c, val)} onSwap={e => onSwap(c, e)} />)}
        </ul>
        {backend.length > 0 && (
          <div style={{ margin: '6px 0 12px', paddingTop: 8, borderTop: `1px dashed ${LINE}` }} data-testid="group-checks-inline">
            <div style={{ ...muted, fontWeight: 700, color: INK }}>Checks</div>
            {backend.map((b, i) => (
              <div key={i}>
                {b.critic && <div style={muted}>Critic: {b.critic.verdict}{(b.critic.reasons || []).length ? ` · ${b.critic.reasons.join(' · ')}` : ''}</div>}
                {(b.critic?.defects || []).map((d, k) => <div key={k} style={{ ...muted, color: d.severity === 'major' ? '#9b1c1c' : INK2 }}>Critic ({d.severity}): {d.flag} {d.detail}</div>)}
                {(b.objective?.checks || []).map((c, k) => <div key={k} style={{ ...muted, color: c.ok || c.warn ? INK2 : '#9b1c1c' }}>{c.ok ? '✓' : c.warn ? '!' : '✗'} {c.name}: {c.detail}</div>)}
              </div>
            ))}
          </div>
        )}
      </div>
    </details>
  )
}

// ── stepper ─────────────────────────────────────────────────────────────────────────────────────
function Stepper({ st }: { st: State }) {
  const cur = stageIndex(st.phase)
  const pct = st.running ? Math.min(96, ((cur + 0.5) / STAGES.length) * 100) : 100
  const last = st.trace.filter(t => t.kind === 'phase').slice(-1)[0]?.msg
  return (
    <div data-testid="stepper">
      <div style={{ ...muted, display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
        <span>{st.running ? (cur < 0 ? 'Waiting for a free slot' : `Step ${cur + 1} of ${STAGES.length}: ${STAGES[cur].label}`) : 'All stages finished'}</span>
        <span>{Math.round(pct)}%</span>
      </div>
      <div style={{ height: 8, borderRadius: 999, background: 'rgba(31,31,31,0.10)', overflow: 'hidden' }} role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} data-testid="progress">
        <div style={{ width: `${pct}%`, height: '100%', background: INK, borderRadius: 999, transition: 'width .8s ease' }} />
      </div>
      <ol style={{ listStyle: 'none', margin: '12px 0 0', padding: 0 }}>
        {STAGES.map((s, i) => {
          const state = !st.running ? 'done' : i < cur ? 'done' : i === cur ? 'now' : 'todo'
          return (
            <li key={s.key} style={{ display: 'flex', gap: 12, alignItems: 'flex-start', padding: '7px 0' }} data-state={state}>
              <span aria-hidden="true" style={{ flex: 'none', width: 20, height: 20, borderRadius: 20, marginTop: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 700,
                background: state === 'done' ? INK : 'transparent', color: state === 'done' ? '#fff' : INK, border: `1.5px solid ${state === 'todo' ? '#cfc9b9' : INK}` }}>
                {state === 'done' ? '✓' : state === 'now' ? <span className="animate-pulse" style={{ width: 8, height: 8, borderRadius: 8, background: INK }} /> : ''}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: state === 'now' ? 700 : 500, color: state === 'todo' ? '#8d8878' : INK }}>{s.label}</div>
                {state === 'now' && (
                  <>
                    <div style={muted}>{last || s.hint}</div>
                    <div style={{ height: 4, borderRadius: 4, background: 'rgba(31,31,31,0.10)', overflow: 'hidden', marginTop: 6, position: 'relative' }}>
                      <div style={{ position: 'absolute', top: 0, bottom: 0, width: '36%', background: INK, borderRadius: 4, animation: 'p-sweep 1.4s ease-in-out infinite' }} />
                    </div>
                  </>
                )}
              </div>
            </li>
          )
        })}
      </ol>
      <details style={{ marginTop: 8 }} data-testid="activity-log">
        <summary style={{ ...muted, cursor: 'pointer', fontWeight: 600 }}>Activity log</summary>
        <ol style={{ margin: '6px 0 0', padding: 0, listStyle: 'none', maxHeight: 220, overflowY: 'auto' }} data-testid="auto-edit-trace">
          {st.trace.slice(-40).map((t, i) => (
            <li key={i} style={{ ...muted, display: 'flex', gap: 8 }}>
              <span style={{ width: 14, flex: 'none' }}>{t.kind === 'kept' ? '✓' : t.kind === 'reverted' ? '↩' : '·'}</span><span>{t.msg}</span>
            </li>
          ))}
        </ol>
      </details>
    </div>
  )
}

// ── verified ────────────────────────────────────────────────────────────────────────────────────
const TH: CSSProperties = { padding: '6px 4px', fontWeight: 600 }

function MeasuredTable({ m0, m1, pauses }: { m0?: Metrics; m1?: Metrics; pauses: number }) {
  const rows: [string, string, string][] = [
    ['Length (s)', fmt(m0?.duration_s), fmt(m1?.duration_s)],
    ['Loudness (LUFS)', fmt(m0?.lufs), fmt(m1?.lufs)],
    ['True peak (dBTP)', fmt(m0?.true_peak_db), fmt(m1?.true_peak_db)],
    ['Silence share', fmt(m0?.silence_ratio, 2), fmt(m1?.silence_ratio, 2)],
    ['Pauses removed', '0', String(pauses)],
    ['Hard cuts (shot changes)', fmt(m0?.cut_count, 0), fmt(m1?.cut_count, 0)],
    ['Average shot (s)', fmt(m0?.avg_shot_s), fmt(m1?.avg_shot_s)],
  ]
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }} data-testid="measured-table">
        <thead><tr style={{ textAlign: 'left', color: INK2 }}><th style={TH}>Measured by tools</th><th style={TH}>Before</th><th style={TH}>After</th></tr></thead>
        <tbody>{rows.map(([l, b, a]) => <tr key={l} style={{ borderTop: `1px solid ${LINE}` }}><td style={{ padding: '6px 4px' }}>{l}</td><td>{b}</td><td>{a}</td></tr>)}</tbody>
      </table>
      <p style={{ ...muted, margin: '6px 0 0' }}>&ldquo;Pauses removed&rdquo; counts the silent gaps the edit cut out. &ldquo;Hard cuts&rdquo; are shot changes in the picture, so pause cuts do not raise it.</p>
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
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead><tr style={{ textAlign: 'left', color: INK2 }}><th style={TH}>Parameter (AI-estimated)</th><th style={TH}>Report</th><th style={TH}>After edit</th><th style={TH}>Change</th></tr></thead>
        <tbody>{rows.map(r => <tr key={r.k} style={{ borderTop: `1px solid ${LINE}` }}><td style={{ padding: '6px 4px' }}>{r.k}</td><td>{r.b}</td><td>{r.a}</td><td style={{ fontWeight: r.d ? 700 : 400 }}>{r.d > 0 ? '+' : ''}{r.d}</td></tr>)}</tbody>
      </table>
    </div>
  )
}

function ManualTips({ tips }: { tips: Tip[] }) {
  const li = (t: Tip, i: number) => <li key={i} style={{ fontSize: 13, lineHeight: 1.5, marginBottom: 6 }}>{t.tip}{t.why_manual ? <span style={{ color: INK2 }}> ({t.why_manual})</span> : null}</li>
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

// ── finish in Instagram Edits ───────────────────────────────────────────────────────────────────
function FinishInEdits({ analysisId, st, busy, call, download, base }: { analysisId: string; st: State; busy: boolean; call: (m: string, p: string, b?: unknown) => Promise<boolean>; download: (k: string, n: string) => Promise<void>; base: string }) {
  const cc = st.clean_cut
  const ready = !!cc?.ready
  const lines = recipeLines(st.changes)
  const [file, setFile] = useState<File | null>(null)
  const [qr, setQr] = useState<string | null>(null)
  const [shareMsg, setShareMsg] = useState('')
  useEffect(() => {
    if (!ready) { setFile(null); return }
    let dead = false
    authFetch(apiUrl(`${base}/download/clean`)).then(r => (r.ok ? r.blob() : null)).then(b => { if (b && !dead) setFile(new File([b], 'prysm-clean-cut.mp4', { type: 'video/mp4' })) }).catch(() => {})
    return () => { dead = true }
  }, [ready, base, cc?.ops])
  const canShareFiles = !!file && typeof navigator !== 'undefined' && typeof navigator.canShare === 'function' && navigator.canShare({ files: [file] })
  useEffect(() => {
    if (!ready || canShareFiles) { setQr(null); return }
    QRCode.toDataURL(`${window.location.origin}/edit/${analysisId}`, { margin: 1, width: 168 }).then(setQr).catch(() => setQr(null))
  }, [ready, canShareFiles, analysisId])
  async function send() {
    if (!file) return
    try { await navigator.share({ files: [file], title: 'Prysm clean cut' }); setShareMsg('') } catch (e) { if ((e as Error)?.name !== 'AbortError') setShareMsg('Sharing did not work here. Download the file and save it to Photos instead.') }
  }
  return (
    <Section title="Finish in Instagram Edits" testid="finish" sub="Direct Edits integration is not available yet; this pack lets you finish the edit in Instagram Edits.">
      <h3 style={{ ...h3, margin: '0 0 6px' }}>1 · Clean cut</h3>
      <p style={{ ...muted, margin: '0 0 8px' }}>The same video with only the safe pause and loudness fixes: no captions, zoom, labels, hook text or style. No AI is used to make it.</p>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        {!ready && <button style={{ ...btn, opacity: busy || cc?.running ? 0.6 : 1 }} disabled={busy || !!cc?.running} onClick={() => call('POST', '/clean-cut')} data-testid="make-clean">{cc?.running ? 'Making the clean cut…' : 'Make a clean cut'}</button>}
        {ready && <button style={btn} onClick={() => download('clean', 'prysm-clean-cut.mp4')} data-testid="download-clean">Download clean cut</button>}
        {ready && <button style={btn2} disabled={busy || !!cc?.running} onClick={() => call('POST', '/clean-cut')}>Make it again</button>}
      </div>
      {cc?.error && <p style={{ ...muted, color: '#9b1c1c', margin: '8px 0 0' }}>{cc.error}</p>}

      <h3 style={{ ...h3, margin: '18px 0 6px' }}>2 · Recipe for Edits</h3>
      {lines.length === 0
        ? <p style={muted}>No overlay was kept in this edit, so the clean cut is the whole edit.</p>
        : <>
          <p style={{ ...muted, margin: '0 0 6px' }}>Re-apply these as Edits&rsquo; own layers. Times are in the clean cut, to a tenth of a second.</p>
          <ol style={{ margin: 0, paddingLeft: 20, listStyle: 'decimal' }} data-testid="recipe">{lines.map((l, i) => <li key={i} style={{ fontSize: 13, lineHeight: 1.5, marginBottom: 4 }}>{l}</li>)}</ol>
        </>}

      <h3 style={{ ...h3, margin: '18px 0 6px' }}>3 · Send to phone</h3>
      {!ready && <p style={muted}>Make the clean cut first.</p>}
      {ready && canShareFiles && (
        <>
          <button style={btn} onClick={send} data-testid="send-phone">Send to phone</button>
          <p style={{ ...muted, margin: '6px 0 0' }}>Pick Photos or Files, or AirDrop, from the share sheet; then open Instagram Edits and import it.</p>
        </>
      )}
      {ready && !canShareFiles && (
        <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }} data-testid="qr-block">
          {qr && <img src={qr} alt="QR code for this page" width={168} height={168} style={{ borderRadius: 8, background: '#fff', padding: 4 }} />}
          <p style={{ ...muted, flex: '1 1 220px', margin: 0 }}>This browser cannot share files. Scan the code with your phone, sign in, download the clean cut there, <b>save it to Photos</b>, then import it in Instagram Edits.</p>
        </div>
      )}
      {shareMsg && <p style={{ ...muted, color: '#9b1c1c' }}>{shareMsg}</p>}
    </Section>
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
  const pair = useSyncedPair()
  const report = useReportScores(analysisId)
  const backToResults = (() => { try { return JSON.parse(localStorage.getItem('prysm_result') || '{}').analysis_id === analysisId } catch { return false } })()
  useEffect(() => { if (data?.state) setPreset(data.state.preset) }, [data?.state?.preset]) // eslint-disable-line react-hooks/exhaustive-deps
  const st = data?.state || null

  const nav = (
    <nav className="border-b border-border px-3 sm:px-8 h-14 flex items-center justify-between sticky top-0 bg-background/90 backdrop-blur-md z-50">
      <a href="/" className="font-logo text-[18px] sm:text-[21px] text-foreground">PRYSM</a>
      <div className="flex items-center gap-3 sm:gap-4">
        <a href="/dashboard" className="text-xs sm:text-sm text-foreground/60 hover:text-foreground transition-colors">Dashboard</a>
        <a href="/analyse" className="text-xs sm:text-sm text-foreground/70 hover:text-foreground transition-colors">New Analysis →</a>
      </div>
    </nav>
  )
  const back = (
    <Link to={backToResults ? '/results' : '/dashboard'} className="text-[13px] text-foreground/70 hover:text-foreground transition-colors font-body" data-testid="back-link">
      ← {backToResults ? 'Back to results' : 'Back to dashboard'}
    </Link>
  )
  const shell = (body: ReactNode) => (
    <div className="min-h-screen bg-background text-foreground">
      {nav}
      <div className="max-w-7xl mx-auto px-3 sm:px-6 py-4 sm:py-6">{back}<div style={{ height: 12 }} />{body}</div>
    </div>
  )

  if (enabled === null || (enabled && !data)) return shell(<p className="text-foreground/60 font-body text-sm">Loading…</p>)
  if (!enabled) return shell(<div className="p-card" style={{ padding: 20 }}><h1 style={h2}>Auto-edit is not available</h1><p style={{ ...muted, margin: '6px 0 0' }}>It is switched off on this server, or you need to sign in again.</p></div>)
  if (!data) return null

  const done = !!st && !st.running && !!st.final
  const groups = groupChanges(st?.changes || [])
  const overall = st?.final?.overall_scores
  const m0 = st?.baseline?.metrics, m1 = st?.final?.metrics
  const toggleItem = (c: Change, v: boolean) => call('POST', '/toggle', { op_id: c.id, enabled: v })
  const toggleGroup = (ids: string[], v: boolean) => (ids.length ? call('POST', '/toggle', { op_ids: ids, enabled: v }) : Promise.resolve(true))
  const swap = (c: Change, effect: string) => call('POST', '/swap', { op_id: c.id, effect })
  const busyAny = busy || !!st?.running || !!st?.clean_cut?.running

  const left = (
    <div className="lg:sticky lg:top-[72px] self-start" data-testid="edit-left">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12 }}>
        <Player title="Before (your upload)" path={data.eligible || st ? `${base}/download/original` : null} version={0} waiting="Not available" onVideo={pair.setA} />
        <Player title="After" path={done ? `${base}/download/mp4` : null} version={version} waiting={st?.running ? 'Working on it…' : 'Your edit appears here'} onVideo={pair.setB} />
      </div>
      {done && (
        <>
          <button style={{ ...btn, width: '100%', marginTop: 12, opacity: pair.ready ? 1 : 0.5 }} disabled={!pair.ready} onClick={pair.toggle} data-testid="play-both">{pair.playing ? 'Pause both' : 'Play both'}</button>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }} data-testid="downloads">
            <button style={btn} onClick={() => download('mp4', 'prysm-edit.mp4')}>Download MP4</button>
            {st?.final?.has_captions && <><button style={btn2} onClick={() => download('srt', 'captions.srt')}>Captions (SRT)</button><button style={btn2} onClick={() => download('ass', 'captions.ass')}>Captions (ASS)</button></>}
            <button style={btn2} onClick={() => download('cover', 'cover.jpg')}>Cover image</button>
            <button style={btn2} onClick={() => download('edl', 'edit-list.json')}>Edit list (JSON)</button>
          </div>
        </>
      )}
    </div>
  )

  const head = (
    <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'space-between', marginBottom: 12 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 24, fontWeight: 800, margin: 0 }}>Auto-edit</h1>
        {st && <span style={tag}>{st.running ? 'working' : st.status === 'done' ? 'done' : st.status}</span>}
        {st?.cost && <span style={{ ...tag, color: INK, borderColor: INK }} data-testid="t-cost">AI cost ${st.cost.total_usd.toFixed(2)}</span>}
      </div>
      {st && !st.running && (
        <div style={{ display: 'flex', gap: 8 }}>
          <button style={btn2} disabled={busyAny} onClick={async () => { if (await call('POST', '/start', { preset: st.preset })) load() }}>Run again</button>
          <button style={btn2} disabled={busyAny} onClick={async () => { if (window.confirm('Delete the edited video and the stored copy of your upload?') && await call('DELETE', '')) setData(d => d && { ...d, state: null, eligible: false, reason: 'Deleted. The stored files for this video were removed.' }) }}>Delete</button>
        </div>
      )}
    </div>
  )

  let right: ReactNode
  if (!st) {
    right = (
      <div className="p-card p-fade" style={{ padding: 20 }} data-testid="edit-start">
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
        {err && <p style={{ ...muted, color: '#9b1c1c' }}>{err}</p>}
      </div>
    )
  } else {
    right = (
      <>
        {head}
        {st.error && <p style={{ ...muted, color: '#9b1c1c', margin: '0 0 12px' }}>{st.status === 'stopped' ? st.error + ' The changes verified so far are kept.' : st.error}</p>}
        {err && <p style={{ ...muted, color: '#9b1c1c', margin: '0 0 12px' }}>{err}</p>}
        {done && <Summary st={st} />}

        {(st.running || !done) && (
          <Section title={st.running ? 'Working on your edit' : 'What happened'}><Stepper st={st} /></Section>
        )}

        {done && <>
          <Section n={1} title="Changes" sub="Open a group to see each change with its exact time and the checks. A safe fix stays when the tool checks pass and the critic finds no defect; a taste call also needs the critic's approval." testid="changes">
            {st.changes.length === 0 && <p style={muted}>The plan had no changes that Prysm can make automatically. See &ldquo;Do it yourself&rdquo; below.</p>}
            <div>{groups.map(g => <GroupCard key={g.key} g={g} st={st} busy={busyAny} onGroup={toggleGroup} onItem={toggleItem} onSwap={swap} />)}</div>
            <p style={{ ...muted, margin: '8px 0 0' }}>{keptChanges(st).length} of {st.changes.length} changes kept. {busyAny ? 'Re-rendering…' : ''}</p>
          </Section>

          <Section n={2} title="Style" sub="A pack sets the headline font, the caption font, the accent colour and the caption animation together." testid="style">
            {st.packs && <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }} data-testid="style-packs">
              {Object.entries(st.packs).map(([k, p]) => (
                <button key={k} disabled={busyAny} onClick={() => call('POST', '/style', { pack: k })} style={{ ...(st.style?.pack === k ? btn : btn2), textAlign: 'left', padding: '8px 14px' }} title={p.for}>
                  <span style={{ display: 'inline-block', width: 9, height: 9, borderRadius: 9, background: `#${p.accent}`, border: `1px solid ${LINE}`, marginRight: 6 }} />{p.label}
                  <div style={{ fontSize: 12, opacity: 0.75, fontWeight: 400 }}>{p.headline} + {p.caption}</div>
                </button>
              ))}
            </div>}
            {st.style_choice && <p style={{ ...muted, margin: '0 0 8px' }} data-testid="pack-note">
              {st.style?.pack && st.style.pack !== st.style_choice.pack
                ? <>Rendered with <b>{st.packs?.[st.style.pack]?.label || st.style.pack}</b> ({st.style.pack_source === 'your choice' ? 'your choice' : 'set by a request'}). Prysm suggested <b>{st.packs?.[st.style_choice.pack]?.label || st.style_choice.pack}</b>.</>
                : <>Suggested and used: <b>{st.packs?.[st.style_choice.pack]?.label || st.style_choice.pack}</b>{st.style_choice.pack_reason ? ` (${st.style_choice.pack_reason})` : ''}.</>} {st.style_choice.emphasis_count} words are emphasised.
            </p>}
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
              <FontPicker label="Headline font" value={st.style?.font_headline} busy={busyAny} onPick={f => call('POST', '/style', { font_headline: f })} />
              <FontPicker label="Caption font" value={st.style?.font_caption} busy={busyAny} onPick={f => call('POST', '/style', { font_caption: f })} />
            </div>
            {(st.style_choice?.fonts || []).length > 0 && <div style={{ marginBottom: 8 }}>
              <div style={{ ...muted, fontWeight: 600, marginBottom: 4 }}>Other fonts that could suit this video (one click):</div>
              {st.style_choice!.fonts.map((f, i) => <button key={i} disabled={busyAny} onClick={() => call('POST', '/style', f.role === 'headline' ? { font_headline: f.family } : { font_caption: f.family })} style={{ ...btn2, padding: '6px 12px', fontSize: 12, marginRight: 6, marginBottom: 6 }} title={f.reason}>{f.role}: {f.family}</button>)}
            </div>}
            {(st.style_notes || []).map((n, i) => <p key={i} style={{ ...muted, color: '#8a5a12', margin: '0 0 6px' }}>{n}</p>)}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 6 }} data-testid="intensity">
              <span style={{ ...muted, fontWeight: 600 }}>Effects:</span>
              {(st.intensities || []).map(k => <button key={k} disabled={busyAny} onClick={() => call('POST', '/style', { intensity: k })} style={{ ...(st.style?.intensity === k ? btn : btn2), padding: '6px 14px', fontSize: 12 }}>{k[0].toUpperCase() + k.slice(1)}</button>)}
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 6 }}>
              <span style={{ ...muted, fontWeight: 600 }}>Look:</span>
              {(st.presets || []).map(p => <button key={p} disabled={busyAny} onClick={() => call('POST', '/preset', { preset: p })} style={{ ...(p === st.preset ? btn : btn2), padding: '6px 14px', fontSize: 12 }}>{p[0].toUpperCase() + p.slice(1)}</button>)}
              <span style={{ ...muted, fontWeight: 600, marginLeft: 8 }}>Sound:</span>
              <label style={{ ...muted, display: 'flex', alignItems: 'center', gap: 6 }}><input type="checkbox" checked={st.style?.sfx !== false} disabled={busyAny} onChange={e => call('POST', '/style', { sfx: e.target.checked })} style={{ width: 16, height: 16, accentColor: INK }} />Sound effects</label>
            </div>
            <p style={{ ...muted, margin: 0 }}>Sound effects are synthesised, mixed far under your voice and never replace your audio. No background music is added.</p>
          </Section>

          <Section n={3} title="Ask for changes" sub="Describe a change in your own words; it is applied, rendered and checked." testid="ask">
            <form onSubmit={async e => { e.preventDefault(); if (text.trim() && await call('POST', '/revise', { text })) setText('') }} style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <input value={text} onChange={e => setText(e.target.value)} placeholder="e.g. bigger captions, less zoom, keep the intro" maxLength={300} aria-label="Ask for a change" style={{ flex: '1 1 220px', minWidth: 0, border: `1px solid ${LINE}`, background: 'transparent', borderRadius: 999, padding: '10px 16px', fontSize: 13 }} />
              <button type="submit" style={btn} disabled={busyAny || st.revisions_left <= 0}>Apply</button>
            </form>
            <div style={{ ...muted, marginTop: 6 }} data-testid="requests-left">{st.revisions_left} of 5 requests left. {st.revisions.length > 0 && 'Done: ' + st.revisions.map(r => `“${r.text}”`).join(', ')}</div>
          </Section>

          <Section n={4} title="Verified" sub={`Measured by tools on the real files.${st.critic?.notes ? ` Critic: ${st.critic.notes}` : ''}`} testid="verified">
            {st.final?.not_rescored && <p style={{ ...muted, margin: '0 0 8px' }}>The latest change was re-checked by tools only; the AI re-score in the details is from the original run.</p>}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }} data-testid="verified-tiles">
              <Tile testid="v-peak" label="True peak" before={m0?.true_peak_db} after={m1?.true_peak_db} unit="dBTP" />
              <Tile testid="v-cuts" label="Hard cuts (shot changes)" before={m0?.cut_count} after={m1?.cut_count} unit="" d={0} />
              <Tile testid="v-shot" label="Average shot" before={m0?.avg_shot_s} after={m1?.avg_shot_s} unit="s" />
            </div>
            <details style={{ marginTop: 12 }} data-testid="verified-details">
              <summary style={{ ...muted, cursor: 'pointer', fontWeight: 700, color: INK }}>Details</summary>
              <div style={{ marginTop: 10 }}>
                <MeasuredTable m0={m0} m1={m1} pauses={pausesRemoved(st)} />
                {overall && <RescoreTable before={overall.before} after={overall.after} report={report} />}
                <details style={{ marginTop: 12 }} data-testid="group-checks">
                  <summary style={{ ...muted, cursor: 'pointer', fontWeight: 600 }}>Checks per change group</summary>
                  {Object.entries(st.groups).map(([g, v]) => (
                    <div key={g} style={{ margin: '10px 0' }}>
                      <div style={{ fontSize: 13, fontWeight: 700 }}>{v.label} — {v.status} · {v.risk === 'safe' ? 'safe fix' : 'taste call'}{v.critic ? ` · critic: ${v.critic.verdict}` : ''}</div>
                      <div style={muted}>{(v.reasons || []).join(' · ')}</div>
                      {(v.critic?.defects || []).map((d, i) => <div key={i} style={{ ...muted, color: d.severity === 'major' ? '#9b1c1c' : INK2 }}>Critic ({d.severity}): {d.flag} {d.detail}</div>)}
                      {(v.objective?.checks || []).map((c, i) => <div key={i} style={{ ...muted, color: c.ok || c.warn ? INK2 : '#9b1c1c' }}>{c.ok ? '✓' : c.warn ? '!' : '✗'} {c.name}: {c.detail}</div>)}
                    </div>
                  ))}
                </details>
              </div>
            </details>
          </Section>

          {st.manual_tips.length > 0 && (
            <Section collapsible title="Do it yourself" count={st.manual_tips.length} sub="Fixes Prysm cannot make for you." testid="manual"><ManualTips tips={st.manual_tips} /></Section>
          )}

          <FinishInEdits analysisId={analysisId || ''} st={st} busy={busyAny} call={call} download={download} base={base} />
        </>}
        {!done && st.manual_tips.length > 0 && !st.running && (
          <Section collapsible title="Do it yourself" count={st.manual_tips.length} testid="manual"><ManualTips tips={st.manual_tips} /></Section>
        )}
      </>
    )
  }

  return shell(
    <div className="grid gap-6 lg:gap-8 lg:grid-cols-[minmax(0,548px)_minmax(0,1fr)]" data-testid="edit-layout">
      {left}
      <div style={{ minWidth: 0 }}>{right}</div>
    </div>,
  )
}
