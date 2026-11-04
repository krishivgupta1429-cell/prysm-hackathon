// Built during the Meta Global AI Developer Hackathon (new work).
// "How to adapt this to your niche" on a STUDIED reel: a few bullets from the creator's own Creator DNA, with the fixed note
// "Borrow the structure, not the content." Only rendered when the backend produced the block (Instagram connected, usable DNA).
export type AdaptBlock = { note: string; niche?: string; bullets: string[]; sampleSize?: number; isFixture?: boolean; citedReels?: { n: number; permalink?: string | null }[] }

export default function AdaptCard({ block }: { block: AdaptBlock }) {
  if (!block || !block.bullets?.length) return null
  return (
    <section className="border border-emerald-200/40 rounded-2xl p-5 sm:p-6 mb-5 bg-emerald-50/20" data-testid="adapt-card" aria-labelledby="adapt-h">
      <p className="text-[10px] font-bold tracking-widest uppercase text-emerald-700/50 mb-1 font-body">From your Creator DNA</p>
      <h2 id="adapt-h" className="font-display text-lg font-bold tracking-tight mb-1">How to adapt this to {block.niche ? `your ${block.niche} niche` : 'your niche'}</h2>
      <p className="text-[13px] font-semibold text-foreground/55 font-body mb-3" data-testid="adapt-note">{block.note || 'Borrow the structure, not the content.'}</p>
      <ul className="flex flex-col gap-2.5">
        {block.bullets.map((b, i) => (
          <li key={i} className="flex gap-2.5 text-[14px] text-foreground/70 font-body leading-[1.65]">
            <span className="mt-[9px] w-1.5 h-1.5 rounded-full bg-emerald-500/60 shrink-0" aria-hidden />
            <span>{b}</span>
          </li>
        ))}
      </ul>
      <p className="text-[11px] text-foreground/30 font-body mt-3">
        Based on your {block.sampleSize ? `${block.sampleSize} imported reels` : 'imported reels'}{block.isFixture ? ' (test data)' : ''}. A small sample, not proof.
      </p>
    </section>
  )
}
