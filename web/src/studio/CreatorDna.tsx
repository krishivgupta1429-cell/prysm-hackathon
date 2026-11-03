// Built during the Meta Global AI Developer Hackathon (new work).
// Creator DNA page (/creator-dna): consent, connect, "how many reels" import with live progress, the DNA summary, the imported reels
// with their index and watch-through, Re-import and Disconnect. Black-and-white, inline styles, mobile friendly.
// Fixture (dev-only fake) data is always labelled so it can never pass for real.
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { apiUrl } from '@/lib/api'
import { authFetch, getToken } from '@/lib/auth'

type Status = {
  configured: boolean
  fixtureAvailable: boolean
  connected: boolean
  scopes: string[]
  limits: { min: number; max: number; default: number }
  account: null | {
    username: string | null; isFixture: boolean; tokenExpiresAt: string | null; lastImportAt: string | null
    lastImportRequested: number | null
    lastImport: null | { imported: number; found: number; requested: number; missing_metrics: string[]; rate_limited: boolean; token_invalid: boolean }
  }
}
type Claim = { claim: string; reels: number[] }
type Fingerprint = { hook_type: string; format: string; pacing: string; audio_type: string; on_screen_text: boolean; topic: string }
type Reel = {
  n: number; id: string; permalink: string | null; postedAt: string | null; caption: string | null; duration_s: number | null
  performance_index: number | null; performance_basis: string | null; watch_through: number | null; fingerprint: Fingerprint | null
  frames_used: number; note: string | null
}
type DnaResp = {
  connected: boolean; isFixture?: boolean; username?: string
  dna: null | {
    summary: string; sampleSize: number; costUsd: number; model: string
    data: { mode: string; reason?: string; strongest: Claim[]; weakest: Claim[]; keep: Claim[]; change: Claim[]; shared?: Claim[]; outlier?: boolean }
    metricsAvailable: Record<string, number>
  }
  reels: Reel[]; metricLabels: Record<string, string>
}
type Job = { status: 'running' | 'done' | 'error'; found?: number; requested: number; imported?: number; error?: string; progress: Record<string, string>
  missing_metrics?: string[]; rate_limited?: boolean; cost_usd?: number }

const page: CSSProperties = { minHeight: '100vh', background: '#fff', color: '#0a0a0a', fontFamily: 'system-ui, -apple-system, sans-serif' }
const wrap: CSSProperties = { maxWidth: 780, margin: '0 auto', padding: '24px 16px 64px' }
const card: CSSProperties = { border: '1px solid #e5e5e5', borderRadius: 16, padding: 20, background: '#fff', marginBottom: 16 }
const h2: CSSProperties = { fontSize: 16, fontWeight: 700, margin: '0 0 8px' }
const p: CSSProperties = { fontSize: 14, lineHeight: 1.55, color: '#404040', margin: '0 0 8px' }
const small: CSSProperties = { fontSize: 12, color: '#737373', lineHeight: 1.45 }
const btn: CSSProperties = { background: '#0a0a0a', color: '#fff', border: '1px solid #0a0a0a', borderRadius: 12, padding: '10px 18px', fontSize: 14, fontWeight: 700, cursor: 'pointer' }
const btnGhost: CSSProperties = { ...btn, background: '#fff', color: '#0a0a0a', border: '1px solid #d4d4d4' }
const chip: CSSProperties = { fontSize: 11, border: '1px solid #d4d4d4', borderRadius: 999, padding: '2px 8px', color: '#525252', whiteSpace: 'nowrap' }

const pct = (v: number | null | undefined) => (v == null ? 'not available' : `${Math.round(v * 100)}%`)
const idx = (v: number | null | undefined) => (v == null ? 'not available' : v.toFixed(2))
const date = (s: string | null) => (s ? new Date(s).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '')
const words = (s: string) => s.replace(/_/g, ' ')

function FixtureBanner() {
  return (
    <div data-testid="fixture-banner" style={{ background: '#0a0a0a', color: '#fff', borderRadius: 12, padding: '10px 14px', fontSize: 13, fontWeight: 600, marginBottom: 16 }}>
      TEST DATA (development fixture). These reels are fake, generated on this computer. Nothing here comes from Instagram.
    </div>
  )
}

function Consent({ status, onConnect, busy, error }: { status: Status; onConnect: () => void; busy: boolean; error: string }) {
  const available = status.configured || status.fixtureAvailable
  return (
    <div style={card} data-testid="consent">
      <h2 style={h2}>Connect your Instagram to get your Creator DNA</h2>
      <p style={p}>Prysm can read your own recent reels and what Instagram measured for them, find what your best and weakest reels have in common, and then compare every new video you analyse with your own patterns.</p>
      <ul style={{ ...p, paddingLeft: 20 }}>
        <li><b>What we read:</b> your own reels (link, date, caption, likes, comments) and their insights (reach, views, watch time, skip rate and similar). Only your account, nothing about other people.</li>
        <li><b>What we store:</b> those numbers, a short fingerprint of each reel (hook, format, pacing) and your DNA summary. <b>We never store your videos.</b> A video is downloaded for a moment to cut about six frames and is deleted right after.</li>
        <li><b>Who processes your frames:</b> the AI model provider (Meta, Muse Spark) receives those frames and the caption to write the fingerprint.</li>
        <li><b>Deleting it:</b> Disconnect removes your Instagram token and every reel, insight and DNA we derived, at once. You can also remove Prysm in Instagram under Settings, Apps and websites.</li>
        <li><b>Permissions asked:</b> {status.scopes.join(', ')}. Read only; Prysm cannot post or message for you.</li>
      </ul>
      <p style={small}>Needs a Business or Creator Instagram account. Signing in to Prysm stays with email or Google; this is an extra step.</p>
      {!available && <p style={{ ...p, fontWeight: 600 }}>Instagram is not configured on this server yet, so connecting is unavailable.</p>}
      {error && <p style={{ ...p, color: '#0a0a0a', fontWeight: 700 }} role="alert">{error}</p>}
      <button style={{ ...btn, opacity: available && !busy ? 1 : 0.4 }} disabled={!available || busy} onClick={onConnect} data-testid="connect">
        {status.fixtureAvailable ? 'Connect (test data, no Instagram)' : 'I understand, continue to Instagram'}
      </button>
    </div>
  )
}

function ClaimList({ title, items }: { title: string; items: Claim[] }) {
  if (!items?.length) return null
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: '#737373', marginBottom: 4 }}>{title}</div>
      {items.map((c, i) => (
        <p key={i} style={{ ...p, margin: '0 0 4px' }}>{c.claim} <span style={small}>(Reel{c.reels.length > 1 ? 's' : ''} {c.reels.join(', ')})</span></p>
      ))}
    </div>
  )
}

export default function CreatorDna() {
  const navigate = useNavigate()
  const [sp, setSp] = useSearchParams()
  const [status, setStatus] = useState<Status | null>(null)
  const [dna, setDna] = useState<DnaResp | null>(null)
  const [count, setCount] = useState(10)
  const [job, setJob] = useState<Job | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [confirmDisconnect, setConfirmDisconnect] = useState(false)
  const timer = useRef<number | null>(null)

  const load = useCallback(async () => {
    const s = await authFetch(apiUrl('/instagram/status'))
    if (!s.ok) { setError('Could not load your Instagram status.'); return }
    const st: Status = await s.json()
    setStatus(st)
    if (st.connected) {
      const d = await authFetch(apiUrl('/instagram/dna'))
      if (d.ok) setDna(await d.json())
      if (st.account?.lastImportRequested) setCount(st.account.lastImportRequested)
    } else setDna(null)
  }, [])

  useEffect(() => {
    if (!getToken()) { navigate('/login'); return }
    setCount(10)
    load()
    return () => { if (timer.current) window.clearTimeout(timer.current) }
  }, [load, navigate])

  useEffect(() => {
    const r = sp.get('instagram')
    if (r === 'error') setError('Instagram did not complete the connection. Nothing was stored. Please try again.')
    if (r) { sp.delete('instagram'); setSp(sp, { replace: true }) }
  }, [sp, setSp])

  async function connect() {
    setBusy(true); setError('')
    try {
      const r = await authFetch(apiUrl('/instagram/connect'), { method: 'POST' })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || 'Could not start the connection.')
      if (j.fixture) { await load(); setBusy(false); return }
      window.location.href = j.url
    } catch (e: any) { setError(e.message || 'Could not start the connection.'); setBusy(false) }
  }

  function poll(jobId: string) {
    timer.current = window.setTimeout(async () => {
      try {
        const r = await authFetch(apiUrl(`/instagram/import/${jobId}`))
        if (r.ok) {
          const j: Job = await r.json()
          setJob(j)
          if (j.status === 'running') return poll(jobId)
          setBusy(false)
          await load()
          return
        }
      } catch { /* network blip: keep polling */ }
      poll(jobId)
    }, 1500)
  }

  async function startImport() {
    setBusy(true); setError(''); setJob({ status: 'running', requested: count, progress: {} })
    try {
      const r = await authFetch(apiUrl('/instagram/import'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ count }) })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || 'Could not start the import.')
      poll(j.jobId)
    } catch (e: any) { setError(e.message || 'Could not start the import.'); setBusy(false); setJob(null) }
  }

  async function disconnect() {
    setBusy(true)
    const r = await authFetch(apiUrl('/instagram/disconnect'), { method: 'DELETE' })
    setBusy(false); setConfirmDisconnect(false)
    if (r.ok) { setJob(null); setDna(null); await load() } else setError('Could not disconnect. Please try again.')
  }

  if (!status) {
    return <div style={page}><div style={wrap}><p style={p}>{error || 'Loading…'}</p></div></div>
  }

  const fixture = !!(dna?.isFixture || status.account?.isFixture)
  const stages = job ? Object.entries(job.progress) : []
  const total = job ? 2 + (job.found ?? job.requested) : 0
  const done = stages.filter(([, v]) => v !== 'running').length
  const running = stages.find(([, v]) => v === 'running')?.[0]
  const label = running === 'fetch_reels' ? 'Finding your reels…' : running === 'creator_dna' ? 'Writing your Creator DNA…'
    : running?.startsWith('reel_') ? `Reading reel ${parseInt(running.slice(5), 10)} of ${job?.found ?? job?.requested}…` : 'Working…'
  const lastMissing = (dna?.dna ? status.account?.lastImport?.missing_metrics : []) || []

  return (
    <div style={page}>
      <div style={wrap}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
          <h1 style={{ fontSize: 24, fontWeight: 800, margin: 0 }}>Creator DNA</h1>
          <span style={{ display: 'flex', gap: 14 }}>
            <Link to="/analyse" style={{ ...small, color: '#0a0a0a' }}>Analyse a video</Link>
            <Link to="/settings" style={{ ...small, color: '#0a0a0a' }}>Settings</Link>
          </span>
        </div>
        {fixture && <FixtureBanner />}
        {error && !(!status.connected) && <p style={{ ...p, fontWeight: 700 }} role="alert">{error}</p>}

        {!status.connected && <Consent status={status} onConnect={connect} busy={busy} error={error} />}

        {status.connected && (
          <>
            <div style={card} data-testid="import-card">
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <h2 style={h2}>{status.account?.lastImportAt ? 'Re-import your reels' : 'Import your reels'}</h2>
                <span style={chip}>{fixture ? 'test data' : `@${status.account?.username || 'connected'}`}</span>
              </div>
              <p style={p}>How many of your most recent reels should we import? More reels make the patterns steadier but cost a little more to process.</p>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', margin: '12px 0' }}>
                <button style={btnGhost} aria-label="fewer reels" disabled={busy || count <= status.limits.min} onClick={() => setCount(c => Math.max(status.limits.min, c - 1))}>−</button>
                <input type="range" aria-label="number of reels" min={status.limits.min} max={status.limits.max} value={count} disabled={busy}
                  onChange={e => setCount(Number(e.target.value))} style={{ flex: '1 1 160px', accentColor: '#0a0a0a' }} data-testid="count" />
                <button style={btnGhost} aria-label="more reels" disabled={busy || count >= status.limits.max} onClick={() => setCount(c => Math.min(status.limits.max, c + 1))}>+</button>
                <b style={{ fontSize: 20, minWidth: 28, textAlign: 'center' }} data-testid="count-value">{count}</b>
              </div>
              {fixture && count > 12 && <p style={small}>The test fixture only has 12 reels.</p>}
              <button style={{ ...btn, opacity: busy ? 0.5 : 1 }} disabled={busy} onClick={startImport} data-testid="import">
                {busy ? 'Importing…' : status.account?.lastImportAt ? 'Re-import' : 'Import'}
              </button>

              {job && job.status === 'running' && (
                <div style={{ marginTop: 16 }} data-testid="progress">
                  <div style={{ height: 8, background: '#f0f0f0', borderRadius: 999, overflow: 'hidden' }}>
                    <div style={{ width: `${Math.min(100, (done / Math.max(1, total)) * 100)}%`, height: '100%', background: '#0a0a0a', transition: 'width .4s' }} />
                  </div>
                  <p style={{ ...small, marginTop: 6 }}>{label} ({done} of {total} steps)</p>
                </div>
              )}
              {job && job.status === 'done' && (
                <p style={{ ...p, marginTop: 14 }} data-testid="import-result">
                  Imported {job.imported} of {job.requested} requested reels.
                  {(job.found ?? 0) < job.requested && ' Your account has fewer reels than requested.'}
                  {job.rate_limited && ' Instagram\'s rate limit was reached, so some reels have no insights.'}
                </p>
              )}
              {job && job.status === 'error' && <p style={{ ...p, fontWeight: 700 }} role="alert">{job.error}</p>}
            </div>

            {dna?.dna && (
              <div style={card} data-testid="dna">
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                  <h2 style={h2}>Your Creator DNA</h2>
                  <span style={chip}>{dna.dna.sampleSize} reels{fixture ? ' · test data' : ''}</span>
                </div>
                {dna.dna.summary.split('\n\n').map((t, i) => <p key={i} style={p}>{t}</p>)}
                <ClaimList title="What your strongest reels share" items={dna.dna.data.strongest} />
                <ClaimList title="What repeats in your weakest" items={dna.dna.data.weakest} />
                <ClaimList title="Keep" items={dna.dna.data.keep} />
                <ClaimList title="Change" items={dna.dna.data.change} />
                <ClaimList title="Seen in these reels" items={dna.dna.data.shared || []} />
                {lastMissing.length > 0 && (
                  <p style={small} data-testid="missing">Instagram returned nothing for: {lastMissing.map(m => dna.metricLabels[m] || m).join(', ')}. The patterns use the metrics that were available.</p>
                )}
                <p style={small}>Medians are used so one standout reel does not define your normal. Fingerprints were written by {dna.dna.model}.</p>
              </div>
            )}

            {dna && dna.reels.length > 0 && (
              <div style={card} data-testid="reels">
                <h2 style={h2}>Imported reels</h2>
                <p style={small}>Index: reach (or views if reach is missing) compared with your own median, so 1.00 is a typical reel of yours. Watch-through: average watch time divided by the reel's length.</p>
                <div style={{ marginTop: 8 }}>
                  {dna.reels.map(r => (
                    <div key={r.id} style={{ borderTop: '1px solid #eee', padding: '12px 0' }} data-testid="reel-row">
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
                        <b style={{ fontSize: 14 }}>Reel {r.n} <span style={{ ...small, fontWeight: 400 }}>{date(r.postedAt)}{r.duration_s ? ` · ${Math.round(r.duration_s)} s` : ''}</span></b>
                        <span style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                          {fixture && <span style={{ ...chip, background: '#0a0a0a', color: '#fff', borderColor: '#0a0a0a' }}>FIXTURE</span>}
                          <span style={chip}>index {idx(r.performance_index)}{r.performance_basis && r.performance_index != null ? ` (${r.performance_basis})` : ''}</span>
                          <span style={chip}>watch-through {pct(r.watch_through)}</span>
                        </span>
                      </div>
                      <p style={{ ...p, margin: '4px 0' }}>{(r.caption || '').slice(0, 140) || 'No caption'}</p>
                      {r.fingerprint && (
                        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '4px 0' }}>
                          {[words(r.fingerprint.hook_type), words(r.fingerprint.format), `${r.fingerprint.pacing} pace`, r.fingerprint.on_screen_text ? 'on-screen text' : 'no on-screen text', r.fingerprint.topic].filter(Boolean).map((t, i) => <span key={i} style={chip}>{t}</span>)}
                        </div>
                      )}
                      {r.note && <p style={small}>{r.note}</p>}
                      {r.permalink && !fixture && <a href={r.permalink} target="_blank" rel="noreferrer" style={{ ...small, color: '#0a0a0a' }}>Open on Instagram</a>}
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div style={{ ...card, borderColor: '#d4d4d4' }}>
              <h2 style={h2}>Disconnect</h2>
              <p style={p}>This deletes your Instagram token and every reel, insight and Creator DNA stored for you. Your Prysm analyses are not touched.</p>
              {!confirmDisconnect ? (
                <button style={btnGhost} onClick={() => setConfirmDisconnect(true)} disabled={busy} data-testid="disconnect">Disconnect Instagram</button>
              ) : (
                <span style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <button style={btn} onClick={disconnect} disabled={busy} data-testid="disconnect-confirm">Yes, delete everything</button>
                  <button style={btnGhost} onClick={() => setConfirmDisconnect(false)}>Cancel</button>
                </span>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
