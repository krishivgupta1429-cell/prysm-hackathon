// Built during the Meta Global AI Developer Hackathon (new work).
// Creator DNA page: "backfill embeddings". Reels imported before picture matching existed have no DINOv2 embedding; this reads
// them again (temporary download, deleted straight away), embeds them on the Modal GPU (cents for a whole account) and shows how
// many of the imported reels now have one. New imports embed their reels automatically.
import { useCallback, useEffect, useRef, useState } from 'react'
import { apiUrl } from '@/lib/api'
import { authFetch } from '@/lib/auth'
import { Button } from '@/components/ui/button'

type St = { connected: boolean; reels?: number; embedded?: number; missing?: number; no_video?: number; model?: boolean }
type Summary = { status: string; error?: string; embedded?: number; skipped_no_video?: number; failed?: number; cost_usd?: number }

export function PictureMatching({ reloadKey, busy: parentBusy }: { reloadKey: number; busy: boolean }) {
  const [st, setSt] = useState<St | null>(null)
  const [running, setRunning] = useState(false)
  const [out, setOut] = useState<Summary | null>(null)
  const [err, setErr] = useState('')
  const timer = useRef<number | null>(null)

  const load = useCallback(async () => {
    try { const r = await authFetch(apiUrl('/instagram/embeddings/status')); if (r.ok) setSt(await r.json()) } catch { /* nothing shown */ }
  }, [])
  useEffect(() => { load(); return () => { if (timer.current) window.clearTimeout(timer.current) } }, [load, reloadKey])

  function poll(jobId: string) {
    timer.current = window.setTimeout(async () => {
      try {
        const r = await authFetch(apiUrl(`/instagram/import/${jobId}`))
        if (r.ok) {
          const j: Summary = await r.json()
          if (j.status === 'running') return poll(jobId)
          setOut(j); setRunning(false); await load(); return
        }
      } catch { /* network blip: keep polling */ }
      poll(jobId)
    }, 1500)
  }

  async function start() {
    setRunning(true); setErr(''); setOut(null)
    try {
      const r = await authFetch(apiUrl('/instagram/embeddings/backfill'), { method: 'POST' })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || 'Could not start.')
      poll(j.jobId)
    } catch (e: any) { setErr(e.message || 'Could not start.'); setRunning(false) }
  }

  if (!st || !st.connected || !st.reels) return null
  const missing = st.missing ?? 0
  const noVideo = st.no_video ?? 0
  return (
    <section className="rounded-2xl border border-border bg-background p-5" data-testid="picture-matching" aria-labelledby="pm-h">
      <p className="text-[10px] font-bold tracking-widest uppercase text-foreground/30 font-body">Picture matching</p>
      <h2 id="pm-h" className="font-display text-xl font-black tracking-tight">{st.embedded} of {st.reels} reels can be compared by how they look</h2>
      {noVideo > 0 && <p className="mt-1 text-[12px] text-foreground/50 font-body" data-testid="no-video-note">{noVideo} reel{noVideo === 1 ? ' has' : 's have'} no video file from Instagram (often licensed audio), so {noVideo === 1 ? 'it' : 'they'} cannot be compared.</p>}
      <p className="mt-1 text-[13px] text-foreground/60 font-body leading-relaxed">
        On a results page this lets Prysm say which of your reels a new video looks most like. It compares pictures only, not results.
        New imports do this automatically; reels you imported earlier need a one-off pass (a few cents of GPU time for a whole account). Videos are downloaded temporarily and deleted.
      </p>
      {missing > 0 && (
        <div className="mt-3 flex items-center gap-3 flex-wrap">
          <Button onClick={start} disabled={running || parentBusy || st.model === false} data-testid="backfill-button">
            {running ? 'Computing…' : `Backfill embeddings (${missing} reel${missing === 1 ? '' : 's'})`}
          </Button>
          {st.model === false && <span className="text-[12px] text-foreground/50 font-body">The picture model is switched off on this server.</span>}
        </div>
      )}
      {err && <p className="mt-2 text-[13px] font-body" role="alert">{err}</p>}
      {out && (
        <p className="mt-2 text-[13px] font-body" data-testid="backfill-result">
          {out.status === 'done'
            ? `Done: ${out.embedded} reel${out.embedded === 1 ? '' : 's'} added${out.skipped_no_video ? `, ${out.skipped_no_video} skipped (Instagram gave no video file)` : ''}${out.failed ? `, ${out.failed} failed` : ''}. Cost ${(out.cost_usd || 0) < 0.001 ? 'under $0.001' : `about $${(out.cost_usd || 0).toFixed(3)}`}.`
            : out.error || 'The backfill failed.'}
        </p>
      )}
    </section>
  )
}
