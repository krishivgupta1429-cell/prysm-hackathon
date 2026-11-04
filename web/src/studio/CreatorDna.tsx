// Built during the Meta Global AI Developer Hackathon (new work).
// The Creator DNA page (/creator-dna): consent and connect, the connected account, import, the DNA, and every imported reel.
// Uses the app's own look (cream background, General Sans / Manrope, border and primary tokens, shadcn components).
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { apiUrl } from '@/lib/api'
import { authFetch, getToken } from '@/lib/auth'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { AccountCard } from './creator/AccountCard'
import { Consent } from './creator/Consent'
import { DnaCard } from './creator/DnaCard'
import { FixLoop } from './creator/FixLoop'
import { ImportCard } from './creator/ImportCard'
import { ReelCard } from './creator/ReelCard'
import type { DnaResp, Job, Status } from './creator/types'

const LABEL = 'text-[10px] font-bold tracking-widest uppercase text-foreground/30 font-body'

export default function CreatorDna() {
  const navigate = useNavigate()
  const [sp, setSp] = useSearchParams()
  const [status, setStatus] = useState<Status | null>(null)
  const [dna, setDna] = useState<DnaResp | null>(null)
  const [avail, setAvail] = useState<{ available: number | null; complete: boolean } | null>(null)
  const [count, setCount] = useState(10)
  const [job, setJob] = useState<Job | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [loadError, setLoadError] = useState('')
  const [openReel, setOpenReel] = useState<number | null>(null)
  const timer = useRef<number | null>(null)
  const importRef = useRef<HTMLDivElement>(null)
  const countSet = useRef(false)
  const [reloadKey, setReloadKey] = useState(0)

  const load = useCallback(async () => {
    setLoadError('')
    try {
      const s = await authFetch(apiUrl('/instagram/status'))
      if (!s.ok) throw new Error('status')
      const st: Status = await s.json()
      setStatus(st)
      if (!st.connected) { setDna(null); return }
      const d = await authFetch(apiUrl('/instagram/dna'))
      if (d.ok) { setDna(await d.json()); setReloadKey(k => k + 1) }
      let mx = st.limits.max
      try {   // how many reels the account has (one cached Instagram call at most)
        const a = await authFetch(apiUrl('/instagram/available'))
        if (a.ok) { const av = await a.json(); setAvail(av); mx = Math.min(st.limits.max, av.available ?? st.limits.max) }
      } catch { /* the plain maximum is offered */ }
      if (!countSet.current) {   // default: 10, or the most the account has when that is fewer
        countSet.current = true
        setCount(Math.max(st.limits.min, Math.min(st.limits.default, mx)))
      } else setCount(c => Math.max(st.limits.min, Math.min(c, mx)))
    } catch { setLoadError('Could not load your Instagram status. Check your connection and try again.') }
  }, [])

  useEffect(() => {
    if (!getToken()) { navigate('/login'); return }
    load()
    return () => { if (timer.current) window.clearTimeout(timer.current) }
  }, [load, navigate])

  useEffect(() => {
    const r = sp.get('instagram')
    if (r === 'error') setError('Instagram did not complete the connection. If your account is personal, switch to a Creator or Business account in Instagram settings, then try again.')
    if (r) { sp.delete('instagram'); setSp(sp, { replace: true }) }
  }, [sp, setSp])

  async function connect() {
    setBusy(true); setError('')
    try {
      const r = await authFetch(apiUrl('/instagram/connect'), { method: 'POST' })
      const j = await r.json().catch(() => ({}))
      if (r.status === 503) throw new Error('Connecting Instagram is not available right now. Please try again later.')
      if (!r.ok) throw new Error(j.detail || 'Could not start the connection.')
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
    setBusy(false)
    if (r.ok) { setJob(null); setDna(null); setAvail(null); countSet.current = false; await load() } else setError('Could not disconnect. Please try again.')
  }

  const gotoImport = () => { importRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }); window.setTimeout(() => importRef.current?.focus(), 350) }

  const fixture = !!(dna?.isFixture || status?.account?.isFixture)
  const reels = dna?.reels || []
  const selected = reels.find(r => r.n === openReel) || null
  const lastMissing = (dna?.dna ? status?.account?.lastImport?.missing_metrics : []) || []
  const maxCount = status ? Math.max(status.limits.min, Math.min(status.limits.max, avail?.available ?? status.limits.max)) : 30

  return (
    <div className="min-h-screen bg-background text-foreground relative">
      <div className="grain-overlay" aria-hidden="true" />
      <nav className="border-b border-border px-4 sm:px-8 h-14 flex items-center justify-between sticky top-0 bg-background/90 backdrop-blur-md z-40">
        <Link to="/" className="font-logo text-[18px] sm:text-[21px] text-foreground">PRYSM</Link>
        <span className="flex items-center gap-5 text-sm font-body text-foreground/45">
          <Link to="/analyse" className="hover:text-foreground/70 transition-colors">Analyse a video</Link>
          <Link to="/settings" className="hover:text-foreground/70 transition-colors">Settings</Link>
        </span>
      </nav>

      <main className="max-w-3xl mx-auto px-4 sm:px-5 py-8 sm:py-10 space-y-5">
        <header>
          <h1 className="font-display text-3xl font-black tracking-tight">Creator DNA</h1>
          <p className="text-foreground/45 text-sm font-body mt-1">What your own reels show about what works for you, from the numbers Instagram measured.</p>
        </header>

        {fixture && (
          <div data-testid="fixture-banner" className="rounded-xl bg-foreground text-background px-4 py-2.5 text-[13px] font-semibold font-body">
            TEST DATA (development fixture). These reels are fake, generated on this computer. Nothing here comes from Instagram.
          </div>
        )}
        {error && <p className="rounded-xl border border-destructive/40 bg-destructive/5 px-4 py-3 text-[13px] font-bold text-destructive font-body" role="alert" data-testid="page-error">{error}</p>}

        {loadError ? (
          <div className="rounded-2xl border border-border p-5" data-testid="load-error">
            <p className="text-[14px] font-body text-foreground/70">{loadError}</p>
            <Button className="mt-3" variant="outline" onClick={load}>Try again</Button>
          </div>
        ) : !status ? (
          <div className="space-y-4" data-testid="loading" aria-busy="true" aria-label="Loading">
            <Skeleton className="h-20 w-full rounded-2xl" /><Skeleton className="h-48 w-full rounded-2xl" /><Skeleton className="h-64 w-full rounded-2xl" />
          </div>
        ) : !status.connected ? (
          <Consent status={status} onConnect={connect} busy={busy} error="" />
        ) : (
          <>
            <AccountCard username={status.account?.username ?? null} pictureUrl={dna?.profilePictureUrl ?? status.account?.profilePictureUrl} fixture={fixture}
              lastImportAt={status.account?.lastImportAt} imported={status.account?.lastImport?.imported} busy={busy} onReimport={gotoImport} onDisconnect={disconnect} />

            <ImportCard ref={importRef} count={count} setCount={setCount} min={status.limits.min} max={maxCount} available={avail} busy={busy}
              hasImport={!!status.account?.lastImportAt} job={job} fixture={fixture} onStart={startImport} />

            {dna?.dna && dna.dna.data.mode === 'breakdown' && (
              <div className="rounded-2xl border border-border bg-background p-5" data-testid="breakdown-note">
                <p className={LABEL}>Creator DNA</p>
                <h2 className="font-display text-xl font-black tracking-tight">Patterns need at least 3 reels</h2>
                <p className="mt-1 text-[14px] text-foreground/60 font-body">
                  You imported {dna.dna.sampleSize} reel{dna.dna.sampleSize === 1 ? '' : 's'}, so below is the breakdown of {dna.dna.sampleSize === 1 ? 'it' : 'each one'} and no pattern claims. Import 3 or more reels to see what they share.
                </p>
              </div>
            )}
            {dna?.dna && dna.dna.data.mode !== 'breakdown' && (
              <DnaCard dna={dna.dna} reels={reels} fixture={fixture} missing={lastMissing} metricLabels={dna.metricLabels} onOpen={setOpenReel} />
            )}

            {reels.length > 0 && <FixLoop reels={reels} reloadKey={reloadKey} onOpen={setOpenReel} />}

            {status.account?.lastImportAt && reels.length === 0 && !busy && (
              <div className="rounded-2xl border border-dashed border-border p-6 text-center" data-testid="empty-reels">
                <p className="font-display text-lg font-black tracking-tight">No reels came through</p>
                <p className="text-[13px] text-foreground/55 font-body mt-1">Instagram returned no reels for this account. Post a reel, or check that this is the right account, then import again.</p>
              </div>
            )}
            {!status.account?.lastImportAt && !busy && (
              <div className="rounded-2xl border border-dashed border-border p-6 text-center" data-testid="empty-import">
                <p className="font-display text-lg font-black tracking-tight">Nothing imported yet</p>
                <p className="text-[13px] text-foreground/55 font-body mt-1">Choose how many reels to read above and press Import.</p>
              </div>
            )}

            {reels.length > 0 && (
              <section data-testid="reels" aria-labelledby="reels-h">
                <div className="flex items-end justify-between gap-2 flex-wrap">
                  <div><p className={LABEL}>Imported</p><h2 id="reels-h" className="font-display text-xl font-black tracking-tight">Your reels</h2></div>
                  {dna?.dna && reels.some(r => r.partial) && (
                    <p className="text-[12px] font-bold font-body" data-testid="analysed-count">{reels.filter(r => !r.partial).length} of {reels.length} reels fully analysed from video; {reels.filter(r => r.partial).length} from thumbnail and caption only.</p>
                  )}
                </div>
                <p className="mt-1 mb-3 text-[12px] text-foreground/45 font-body leading-relaxed" data-testid="vs-note">
                  &ldquo;Above or below your usual&rdquo; compares a reel&rsquo;s reach (or views, when reach is missing) with the median of the {reels.length} reel{reels.length === 1 ? '' : 's'} you imported.
                  With few reels it is a rough guide, not proof. Watch-through is average watch time divided by the reel&rsquo;s length.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {reels.map(r => <ReelCard key={r.id} reel={r} fixture={fixture} onOpen={setOpenReel} />)}
                </div>
              </section>
            )}
          </>
        )}
      </main>

      <Dialog open={selected != null} onOpenChange={o => { if (!o) setOpenReel(null) }}>
        <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto font-body" data-testid="reel-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">Reel {selected?.n}</DialogTitle>
            <DialogDescription className="sr-only">Details of this reel</DialogDescription>
          </DialogHeader>
          {selected && <ReelCard reel={selected} fixture={fixture} detail />}
        </DialogContent>
      </Dialog>
    </div>
  )
}
