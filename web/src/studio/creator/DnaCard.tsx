// Built during the Meta Global AI Developer Hackathon (new work).
// The Creator DNA: each pattern is shown once, with chips for the reels it comes from (a chip opens that reel). No repeated prose.
import type { Claim, DnaResp, Reel } from './types'

const LABEL = 'text-[10px] font-bold tracking-widest uppercase text-foreground/30 font-body'

/** The claim text without a trailing "in Reels 2, 3" (the chips say it). */
export function cleanClaim(text: string): string {
  const t = text.replace(/[\s,;]*\(?\b(?:(?:such as|as|like)\s+)?(?:(?:seen\s+)?(?:in|from)\s+)?Reels?\s+\d+(?:\s*(?:,|and|&)\s*(?:Reels?\s+)?\d+)*\)?\s*\.?$/i, '').trim()
  const plain = t.replace(/_/g, ' ')   // "visual_only" -> "visual only"
  return plain ? plain[0].toUpperCase() + plain.slice(1) : text
}

export function ReelChips({ reels, onOpen }: { reels: number[]; onOpen: (n: number) => void }) {
  return (
    <span className="inline-flex flex-wrap gap-1" data-testid="reel-chips">
      {reels.map(n => (
        <button key={n} type="button" onClick={() => onOpen(n)} aria-label={`Open reel ${n}`}
          className="text-[11px] font-bold font-body border border-foreground/25 rounded-full px-2 py-0.5 text-foreground/70 hover:bg-primary hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring outline-none transition-colors">
          Reel {n}
        </button>
      ))}
    </span>
  )
}

function Section({ title, items, onOpen }: { title: string; items?: Claim[]; onOpen: (n: number) => void }) {
  if (!items?.length) return null
  return (
    <section className="mt-5">
      <h3 className={LABEL}>{title}</h3>
      <ul className="mt-2 space-y-2.5">
        {items.map((c, i) => (
          <li key={i} className="flex items-start justify-between gap-3 flex-wrap sm:flex-nowrap" data-testid="claim">
            <span className="text-[14px] leading-snug text-foreground/80 font-body min-w-0">{cleanClaim(c.claim)}</span>
            <ReelChips reels={c.reels} onOpen={onOpen} />
          </li>
        ))}
      </ul>
    </section>
  )
}

export function DnaCard({ dna, reels, fixture, missing, metricLabels, onOpen }: {
  dna: NonNullable<DnaResp['dna']>; reels: Reel[]; fixture: boolean; missing: string[]; metricLabels: Record<string, string>; onOpen: (n: number) => void
}) {
  const d = dna.data
  const partial = reels.filter(r => r.partial)
  return (
    <div className="rounded-2xl border border-border bg-background p-5" data-testid="dna">
      <div className="flex items-start justify-between gap-2 flex-wrap">
        <div>
          <p className={LABEL}>Creator DNA</p>
          <h2 className="font-display text-xl font-black tracking-tight" data-testid="dna-title">Based on {dna.sampleSize} reel{dna.sampleSize === 1 ? '' : 's'}</h2>
        </div>
        {fixture && <span className="text-[10px] font-bold tracking-wide uppercase border border-border rounded-full px-2 py-0.5">Test data</span>}
      </div>
      {d.mode === 'descriptive' && d.reason && <p className="mt-2 text-[13px] text-foreground/60 font-body" data-testid="dna-reason">{d.reason}</p>}

      {d.mode === 'full' ? (
        <>
          <Section title="What your strongest reels share" items={d.strongest} onOpen={onOpen} />
          <Section title="What repeats in your weakest" items={d.weakest} onOpen={onOpen} />
          <Section title="Keep doing" items={d.keep} onOpen={onOpen} />
          <Section title="Change" items={d.change} onOpen={onOpen} />
        </>
      ) : <Section title="What these reels have in common" items={d.shared} onOpen={onOpen} />}

      {partial.length > 0 && (
        <div className="mt-5 rounded-xl border border-dashed border-foreground/50 px-3.5 py-3" data-testid="partial-notice">
          <p className="text-[13px] font-bold font-body">Not fully analysed: {partial.map(r => `Reel ${r.n}`).join(', ')}</p>
          <p className="mt-1 text-[12px] text-foreground/55 font-body leading-relaxed">
            Instagram gave no video file for {partial.length === 1 ? 'this reel' : 'these reels'} (often licensed audio), so {partial.length === 1 ? 'it was' : 'they were'} read from the
            thumbnail and caption only. {partial.length === 1 ? 'It has' : 'They have'} no watch-through, and {partial.length === 1 ? 'its' : 'their'} hook, format and pacing were not
            judged. The patterns above rest on the other {reels.length - partial.length} reel{reels.length - partial.length === 1 ? '' : 's'} for those.
          </p>
        </div>
      )}
      <div className="mt-4 space-y-1 text-[12px] text-foreground/45 font-body leading-relaxed">
        {!!d.grounding?.dropped && <p data-testid="grounding-note">{d.grounding.dropped} point{d.grounding.dropped === 1 ? '' : 's'} the checker could not back with your reels {d.grounding.dropped === 1 ? 'was' : 'were'} left out. Every point shown names the reels it comes from.</p>}
        {missing.length > 0 && <p data-testid="missing">Instagram returned nothing for: {missing.map(m => metricLabels[m] || m).join(', ')}. The patterns use the metrics that were available.</p>}
        {d.outlier && <p>One reel did far better than the rest, so the comparison uses medians and it does not define your normal.</p>}
        <p>These are patterns in a small sample, not proof. Fingerprints were written by {dna.model}.</p>
      </div>
    </div>
  )
}
