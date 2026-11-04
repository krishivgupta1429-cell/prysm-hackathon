// Built during the Meta Global AI Developer Hackathon (new work).
// "Closest to your Reel N" on the results page: which of the creator's own imported reels looks most like this video (DINOv2
// picture embeddings), with that reel's REAL stored numbers in plain words. It ranks by how the pictures look, not by results,
// and says so. With fewer than 3 imported reels the ranking carries a small-sample note. Renders nothing when Instagram is not
// connected; says why when the picture comparison is not available.
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiUrl } from '@/lib/api'
import { authFetch, getToken } from '@/lib/auth'

export type Closest = {
  connected?: boolean
  available?: boolean
  reason?: string
  isFixture?: boolean
  note: string
  own_reels?: number
  compared_with?: number
  missing_embeddings?: number
  small_sample?: boolean
  small_sample_note?: string | null
  ranked: { n: number; similarity: number; band: string; permalink: string | null; postedAt: string | null; partial: boolean; lines: string[]; hookWords: string | null }[]
}

const LABEL = 'text-[10px] font-bold tracking-widest uppercase text-foreground/30 font-body'
const EMOJI = "'Manrope', 'Apple Color Emoji', 'Segoe UI Emoji', 'Noto Color Emoji', sans-serif"

export function ClosestReelView({ c, embedded = false }: { c: Closest; embedded?: boolean }) {
  const best = c.ranked[0]
  const shell = embedded ? 'mt-4 pt-4 border-t border-border' : 'rounded-2xl border border-border bg-background p-5 mb-5 text-foreground'
  return (
    <div className={shell} data-testid="closest-reel">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <p className={LABEL}>Looks most like your own reels</p>
        <span className="inline-block text-[9px] font-bold tracking-[0.12em] uppercase px-2 py-0.5 border border-border rounded-full text-foreground/45 whitespace-nowrap font-body">estimate</span>
      </div>
      {c.available && best ? (
        <>
          <h3 className="font-display text-[17px] font-black tracking-tight mt-1" data-testid="closest-headline">
            Closest to your Reel {best.n} <span className="font-body text-[13px] font-bold text-foreground/50">· {best.band} (similarity {best.similarity.toFixed(2)})</span>
          </h3>
          <p className="text-[12px] text-foreground/55 font-body mt-0.5">{c.isFixture ? 'TEST DATA from the development fixture. ' : ''}Similarity runs from 0 (nothing alike) to 1 (the same pictures).</p>
          <ul className="divide-y divide-border mt-2" data-testid="closest-list">
            {c.ranked.map((r, i) => (
              <li key={r.n} className="py-2.5" data-testid="closest-item">
                <p className="text-[13px] font-body">
                  <b>Reel {r.n}</b>{' '}<span className="text-foreground/50">· {r.band} (similarity {r.similarity.toFixed(2)}){i === 0 ? ' · closest' : ''}</span>
                  {r.permalink && !c.isFixture && <> · <a href={r.permalink} target="_blank" rel="noreferrer" className="underline underline-offset-2 text-foreground/60">open on Instagram</a></>}
                </p>
                <p className="text-[12px] text-foreground/65 font-body mt-0.5" data-testid="closest-lines">{r.lines.join(' · ')}</p>
                {i === 0 && r.hookWords && <p className="text-[12px] text-foreground/45 font-body mt-0.5" style={{ fontFamily: EMOJI }}>Opens with &ldquo;{r.hookWords}&rdquo;</p>}
                {r.partial && <p className="text-[11px] text-foreground/40 font-body mt-0.5">Read from thumbnail and caption only; the video file was not available.</p>}
              </li>
            ))}
          </ul>
          {c.small_sample && c.small_sample_note && <p className="text-[12px] font-body mt-2 leading-snug" data-testid="closest-small-sample">{c.small_sample_note}</p>}
          {!!c.missing_embeddings && (
            <p className="text-[11px] text-foreground/40 font-body mt-2" data-testid="closest-missing">
              {c.missing_embeddings} of your {c.own_reels} imported reel{c.own_reels === 1 ? '' : 's'} could not be compared yet.{' '}
              <Link to="/creator-dna" className="underline underline-offset-2">Compute picture matching</Link>
            </p>
          )}
        </>
      ) : (
        <p className="text-[13px] text-foreground/60 font-body mt-1" data-testid="closest-unavailable">
          Picture comparison is not available for this video. {c.reason}
        </p>
      )}
      <p className="text-[12px] font-bold font-body mt-3" data-testid="closest-honest-note">{c.note}</p>
    </div>
  )
}

export default function ClosestReel({ analysisId, embedded = false }: { analysisId?: string; embedded?: boolean }) {
  const [c, setC] = useState<Closest | null>(null)
  useEffect(() => {
    if (!analysisId || !getToken()) return
    let live = true
    authFetch(apiUrl(`/instagram/closest/${analysisId}`))
      .then(r => (r.ok ? r.json() : null))
      .then((j: Closest | null) => { if (live && j && j.connected) setC(j) })
      .catch(() => { /* nothing is shown */ })
    return () => { live = false }
  }, [analysisId])
  return c ? <ClosestReelView c={c} embedded={embedded} /> : null
}
