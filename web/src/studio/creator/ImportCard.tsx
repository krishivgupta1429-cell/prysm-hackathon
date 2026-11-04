// Built during the Meta Global AI Developer Hackathon (new work).
// Import card: how many reels (slider, +/- and a typed number; 1 up to the most the account has, max 30), time estimate, a clear
// per-reel progress state, and plain messages when Instagram's rate limit stops the import (what was read is kept).
import { forwardRef } from 'react'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import type { Job } from './types'
import { estimate } from './format'

const LABEL = 'text-[10px] font-bold tracking-widest uppercase text-foreground/30 font-body'

const stepper = 'h-10 w-10 shrink-0 rounded-lg border border-input bg-background text-lg leading-none hover:bg-accent disabled:opacity-40 focus-visible:ring-2 focus-visible:ring-ring outline-none'

export const ImportCard = forwardRef<HTMLDivElement, {
  count: number; setCount: (n: number) => void; min: number; max: number; available: { available: number | null; complete: boolean } | null
  busy: boolean; hasImport: boolean; job: Job | null; fixture: boolean; onStart: () => void
}>(function ImportCard({ count, setCount, min, max, available, busy, hasImport, job, fixture, onStart }, ref) {
  const clamp = (n: number) => Math.max(min, Math.min(max, n))
  const stages = job ? Object.entries(job.progress || {}) : []
  const reelStages = stages.filter(([k]) => k.startsWith('reel_')).sort(([a], [b]) => a.localeCompare(b))
  const total = job ? 2 + (job.found ?? job.requested) : 0
  const done = stages.filter(([, v]) => v !== 'running').length
  const running = stages.find(([, v]) => v === 'running')?.[0]
  const label = running === 'fetch_reels' ? 'Finding your reels…' : running === 'creator_dna' ? 'Writing your Creator DNA…'
    : running?.startsWith('reel_') ? `Reading reel ${parseInt(running.slice(5), 10)} of ${job?.found ?? job?.requested}…` : 'Starting…'
  const nothing = max < min + 0 || max === 0
  return (
    <div ref={ref} tabIndex={-1} className="rounded-2xl border border-border bg-background p-5 outline-none" data-testid="import-card">
      <p className={LABEL}>{hasImport ? 'Re-import' : 'Import'}</p>
      <h2 className="font-display text-xl font-black tracking-tight">How many reels should we read?</h2>
      <p className="mt-1 text-[13px] text-foreground/55 font-body">We read your most recent reels and what Instagram measured for them. More reels make patterns steadier; patterns need at least 3.</p>

      <div className="mt-4 flex items-center gap-3 flex-wrap">
        <button type="button" className={stepper} aria-label="fewer reels" disabled={busy || count <= min} onClick={() => setCount(clamp(count - 1))}>−</button>
        <input type="range" aria-label="number of reels" min={min} max={Math.max(min, max)} value={clamp(count)} disabled={busy || max <= min}
          onChange={e => setCount(clamp(Number(e.target.value)))} className="flex-1 min-w-[140px] accent-foreground h-2" data-testid="count" />
        <button type="button" className={stepper} aria-label="more reels" disabled={busy || count >= max} onClick={() => setCount(clamp(count + 1))}>+</button>
        <input type="number" inputMode="numeric" aria-label="type a number of reels" min={min} max={max} value={count} disabled={busy} data-testid="count-input"
          onChange={e => { const n = parseInt(e.target.value, 10); if (!Number.isNaN(n)) setCount(clamp(n)) }}
          className="w-16 h-10 rounded-lg border border-input bg-background px-2 text-center text-base font-body focus-visible:ring-2 focus-visible:ring-ring outline-none" />
      </div>
      <p className="mt-2 text-[12px] text-foreground/50 font-body">
        <span data-testid="available">{available?.available != null
          ? `You have ${available.complete ? available.available : `${available.available} or more`} reel${available.available === 1 && available.complete ? '' : 's'} available`
          : 'Could not count your reels right now'}</span>
        {available?.available != null && !available.complete ? ` (the most you can import at once is ${max})` : ''}
        {fixture && available?.available != null ? ' in the test fixture' : ''}. Reading {count} reel{count === 1 ? '' : 's'} takes <span data-testid="estimate">{estimate(count)}</span>.
      </p>

      <div className="mt-4 flex items-center gap-3 flex-wrap">
        <Button onClick={onStart} disabled={busy || nothing} data-testid="import">{busy ? 'Importing…' : hasImport ? 'Re-import' : 'Import'}</Button>
      </div>

      {job?.status === 'running' && (
        <div className="mt-5" data-testid="progress" role="status" aria-live="polite">
          <Progress value={Math.min(100, (done / Math.max(1, total)) * 100)} className="h-2" />
          <p className="mt-2 text-[12px] text-foreground/55 font-body">{label} ({done} of {total} steps)</p>
          {reelStages.length > 0 && (
            <ol className="mt-2 flex flex-wrap gap-1.5" aria-label="Progress per reel">
              {reelStages.map(([k, v]) => (
                <li key={k} className={`text-[11px] font-body rounded-full border px-2 py-0.5 ${v === 'ok' ? 'border-foreground/40 bg-primary text-foreground' : v === 'error' ? 'border-destructive/60 text-destructive' : v === 'skipped' ? 'border-dashed border-border text-foreground/40' : 'border-border text-foreground/55 animate-pulse'}`}
                  data-state={v}>
                  Reel {parseInt(k.slice(5), 10)} {v === 'ok' ? '✓' : v === 'error' ? 'problem' : v === 'skipped' ? 'skipped' : '…'}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
      {job?.status === 'done' && (
        <div className="mt-4 text-[13px] text-foreground/70 font-body space-y-1" data-testid="import-result">
          <p>Imported {job.imported} of {job.requested} requested reels.{(job.found ?? 0) < job.requested && ' Your account has fewer reels than requested.'}</p>
          {!!job.partial && <p>{job.partial} of them had no video file from Instagram (often licensed audio), so {job.partial === 1 ? 'it was' : 'they were'} read from the thumbnail and caption only and {job.partial === 1 ? 'is' : 'are'} not fully analysed.</p>}
          {job.rate_limited && <p className="font-bold" data-testid="rate-limited">Instagram&rsquo;s rate limit was reached, so we stopped. The reels already read were kept; some may have no insights yet. Try again in a little while.</p>}
          {job.token_invalid && <p className="font-bold">Instagram rejected the connection part-way. The reels already read were kept; please disconnect and connect again.</p>}
        </div>
      )}
      {job?.status === 'error' && <p className="mt-4 text-[13px] font-bold text-destructive font-body" role="alert" data-testid="import-error">{job.error}</p>}
    </div>
  )
})
