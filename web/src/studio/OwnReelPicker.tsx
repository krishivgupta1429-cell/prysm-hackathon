// Built during the Meta Global AI Developer Hackathon (new work).
// "Pick from my connected Instagram reels" in the link flow: the creator's imported reels with thumbnails and their real numbers (the same
// ReelCard as the Creator DNA page), or a "Connect Instagram" call to action when nothing is connected. Picking a reel hands its link back.
import { useEffect, useState } from 'react'
import { apiUrl } from '@/lib/api'
import { getToken } from '@/lib/auth'
import { ReelCard } from './creator/ReelCard'
import type { DnaResp, Reel } from './creator/types'

const SHOWN = 6

export default function OwnReelPicker({ pickedUrl, onPick }: { pickedUrl: string; onPick: (permalink: string, reel: Reel) => void }) {
  const [data, setData] = useState<DnaResp | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [all, setAll] = useState(false)

  useEffect(() => {
    let live = true
    const tk = getToken()
    fetch(apiUrl('/instagram/dna'), { headers: tk ? { Authorization: `Bearer ${tk}` } : {} })
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: DnaResp) => { if (live) { setData(d); setState('ready') } })
      .catch(() => { if (live) setState('error') })
    return () => { live = false }
  }, [])

  if (state === 'loading') return <p className="text-sm text-foreground/35 font-body text-center py-6" data-testid="picker-loading">Loading your Instagram reels…</p>
  if (state === 'error') {
    return <p className="text-sm text-foreground/45 font-body text-center py-4" data-testid="picker-error">
      Your Instagram reels could not be loaded right now. You can still paste the link of your reel below.</p>
  }
  if (!data || !data.connected) {
    return (
      <div className="rounded-2xl border border-dashed border-border p-5 text-center" data-testid="picker-connect">
        <p className="font-display text-[15px] font-bold mb-1">Pick from your own Instagram reels</p>
        <p className="text-[13px] text-foreground/45 font-body leading-relaxed mb-4">
          Connect your Instagram (Business or Creator account) to choose a reel from a list with its real numbers.
          You can also paste the link of your reel below.</p>
        <a href="/creator-dna" className="inline-block px-5 py-2.5 rounded-xl bg-foreground text-background text-[13px] font-display font-bold hover:opacity-90 transition-opacity">
          Connect Instagram
        </a>
      </div>
    )
  }
  const reels = data.reels || []
  if (reels.length === 0) {
    return <p className="text-sm text-foreground/45 font-body text-center py-4" data-testid="picker-empty">
      Instagram is connected but no reels are imported yet. Import some on the Creator DNA page, or paste the link of your reel below.</p>
  }
  const shown = all ? reels : reels.slice(0, SHOWN)
  return (
    <div data-testid="picker-list">
      <p className="text-[11px] font-semibold tracking-[0.2em] uppercase text-foreground/30 mb-3 font-body">
        Your reels{data.username ? ` · @${data.username}` : ''}</p>
      <div className="flex flex-col gap-3">
        {shown.map(r => {
          const picked = !!r.permalink && pickedUrl === r.permalink
          return (
            <div key={r.id} className={`rounded-2xl ${picked ? 'ring-2 ring-green-500/50' : ''}`}>
              <ReelCard reel={r} fixture={!!data.isFixture} />
              <button type="button" disabled={!r.permalink} aria-pressed={picked} data-testid="pick-reel"
                onClick={() => r.permalink && onPick(r.permalink, r)}
                className={`mt-2 w-full py-2.5 rounded-xl text-[13px] font-display font-bold transition-all ${
                  !r.permalink ? 'bg-foreground/5 text-foreground/25 cursor-not-allowed'
                    : picked ? 'bg-green-600 text-white' : 'bg-foreground text-background hover:opacity-90 cursor-pointer'}`}>
                {!r.permalink ? 'No link for this reel' : picked ? 'Picked ✓' : `Use Reel ${r.n}`}
              </button>
            </div>
          )
        })}
      </div>
      {reels.length > SHOWN && (
        <button type="button" onClick={() => setAll(a => !a)} className="mt-3 w-full text-[13px] text-foreground/45 font-body underline underline-offset-2 cursor-pointer">
          {all ? 'Show fewer' : `Show all ${reels.length} reels`}</button>
      )}
    </div>
  )
}
