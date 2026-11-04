// Built during the Meta Global AI Developer Hackathon (new work).
// "Closest to your Reel N" on the results page: which of the creator's own imported reels looks most like this video (DINOv2
// picture embeddings), with that reel's REAL stored numbers in plain words. It ranks by how the pictures look, not by results,
// and says so. With fewer than 3 imported reels the ranking carries a small-sample note. Renders NOTHING unless there is a real ranked
// result: not connected, comparison not available or an empty ranking all leave the results page without this card.
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiUrl } from '@/lib/api'
import { authFetch, getToken } from '@/lib/auth'
import { useCountUp } from '@/lib/motion'

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
  ranked: { n: number; thumbnailUrl?: string | null; similarity: number; band: string; permalink: string | null; postedAt: string | null; partial: boolean; lines: string[]; hookWords: string | null }[]
}

const LABEL = 'text-[11px] font-bold tracking-widest uppercase text-foreground/60 font-body'
const EMOJI = "'Manrope', 'Apple Color Emoji', 'Segoe UI Emoji', 'Noto Color Emoji', sans-serif"

function Thumb({ url, n }: { url?: string | null; n: number }) {
  const [bad, setBad] = useState(false)
  const src = url ? (url.startsWith('/') ? apiUrl(url) : url) : null
  return (
    <div style={{ width: 64, height: 114, borderRadius: 10, overflow: 'hidden', flex: 'none', background: 'rgba(31,31,31,0.08)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} data-testid="closest-thumb">
      {src && !bad ? <img src={src} alt={`Reel ${n}`} onError={() => setBad(true)} referrerPolicy="no-referrer" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        : <span style={{ fontSize: 12, color: '#55524b', textAlign: 'center', padding: 4 }}>Reel {n}</span>}
    </div>
  )
}

function SimilarityBar({ value, band }: { value: number; band: string }) {
  const v = useCountUp(value * 100)
  return (
    <div style={{ marginTop: 8 }} data-testid="closest-bar">
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#55524b', marginBottom: 4, gap: 8 }}>
        <span>{band}</span><span>similarity {value.toFixed(2)} of 1</span>
      </div>
      <div style={{ height: 8, borderRadius: 999, background: 'rgba(31,31,31,0.08)', overflow: 'hidden' }}>
        <div style={{ width: `${Math.max(2, Math.min(100, v ?? 0))}%`, height: '100%', background: '#1f1f1f', borderRadius: 999 }} />
      </div>
    </div>
  )
}

export function ClosestReelView({ c, embedded = false }: { c: Closest; embedded?: boolean }) {
  const best = c.ranked?.[0]
  if (!c.available || !best) return null          // nothing real to show: no card, no "not available" text on the results page
  const others = c.ranked.slice(1)
  const shell = embedded ? 'mt-4 pt-4 border-t border-border' : 'p-card p-fade p-4 sm:p-5 mb-4'
  return (
    <div className={shell} data-testid="closest-reel">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <p className={LABEL}>Looks most like your own reels</p>
        <span className="inline-block text-[10px] font-bold tracking-[0.1em] uppercase px-2 py-0.5 border border-border rounded-full text-foreground/70 whitespace-nowrap font-body">estimate</span>
      </div>
      <div style={{ display: 'flex', gap: 14, marginTop: 10, alignItems: 'flex-start' }} data-testid="closest-item">
        <Thumb url={best.thumbnailUrl} n={best.n} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <h3 className="font-display text-[17px] font-black tracking-tight" data-testid="closest-headline">Closest to your Reel {best.n}</h3>
          <SimilarityBar value={best.similarity} band={best.band} />
          <p className="text-[13px] text-foreground/80 font-body mt-2" data-testid="closest-lines">{best.lines.join(' · ')}</p>
          {best.hookWords && <p className="text-[12px] text-foreground/65 font-body mt-1" style={{ fontFamily: EMOJI }}>Opens with &ldquo;{best.hookWords}&rdquo;</p>}
          {best.partial && <p className="p-note mt-1">Read from thumbnail and caption only; the video file was not available.</p>}
          {best.permalink && !c.isFixture && <a href={best.permalink} target="_blank" rel="noreferrer" className="text-[12px] underline underline-offset-2 text-foreground/70 font-body inline-block mt-1">Open on Instagram</a>}
        </div>
      </div>
      <p className="p-note mt-2">{c.isFixture ? 'TEST DATA from the development fixture. ' : ''}Similarity runs from 0 (nothing alike) to 1 (the same pictures).</p>
      {others.length > 0 && (
        <details className="mt-2" data-testid="closest-others">
          <summary className="cursor-pointer text-[13px] font-semibold font-body text-foreground/80">See others ({others.length})</summary>
          <ul className="divide-y divide-border mt-1" data-testid="closest-list">
            {others.map(r => (
              <li key={r.n} className="py-2.5" data-testid="closest-item">
                <p className="text-[13px] font-body"><b>Reel {r.n}</b> <span className="text-foreground/70">· {r.band} (similarity {r.similarity.toFixed(2)})</span></p>
                <p className="text-[12px] text-foreground/70 font-body mt-0.5" data-testid="closest-lines">{r.lines.join(' · ')}</p>
              </li>
            ))}
          </ul>
        </details>
      )}
      {c.small_sample && c.small_sample_note && <p className="text-[12px] font-body mt-2 leading-snug" data-testid="closest-small-sample">{c.small_sample_note}</p>}
      {!!c.missing_embeddings && !!c.compared_with && (
        <p className="p-note mt-2" data-testid="closest-missing">
          {c.missing_embeddings} of your {c.own_reels} imported reel{c.own_reels === 1 ? '' : 's'} could not be compared yet.{' '}
          <Link to="/creator-dna" className="underline underline-offset-2">Compute picture matching</Link>
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
