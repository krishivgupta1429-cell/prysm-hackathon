// Built during the Meta Global AI Developer Hackathon (new work).
// The results-page card "How this compares to your last N reels", and the small "Personalised from your reels" tag.
// Plain-language numbers (reach vs the creator's usual, watch-through) and the real first spoken words of the reels it cites.
// Patterns in a small sample, never proof. Uses the app's own look.
import { Link } from 'react-router-dom'

export type CreatorDnaBlock = {
  personalised: boolean
  sampleSize: number
  isFixture?: boolean
  newFingerprint?: { hook_type: string; format: string; pacing: string; audio_type: string; on_screen_text: boolean; topic: string } | null
  newHookWords?: string | null
  comparison: {
    field: string; label: string; value: string | boolean
    top_match: number; top_total: number; top_reels: number[]
    bottom_match: number; bottom_total: number; bottom_reels: number[]
    verdict: 'top' | 'bottom' | 'neutral'
  }[]
  topReels: number[]
  bottomReels: number[]
  partialReels?: number[]   // read from thumbnail and caption only: not used for hook, format or pacing
  citedReels?: { n: number; group: 'strongest' | 'weakest'; vsUsual: string | null; watchThrough: number | null; hookWords: string | null; partial: boolean }[]
  grounding?: { checked: number; dropped: number; rewritten: number; regenerated: boolean } | null
}

const LABEL = 'text-[10px] font-bold tracking-widest uppercase text-foreground/30 font-body'
const VERDICT = { top: 'Like your stronger reels', bottom: 'Like your weaker reels', neutral: 'No clear difference' }
const val = (v: string | boolean) => (typeof v === 'boolean' ? (v ? 'yes' : 'no') : v)
const EMOJI = "'Manrope', 'Apple Color Emoji', 'Segoe UI Emoji', 'Noto Color Emoji', sans-serif"

export function PersonalisedTag({ fixture }: { fixture?: boolean }) {
  return (
    <span className="inline-block text-[9px] font-bold tracking-[0.12em] uppercase px-2 py-0.5 border border-border rounded-full text-foreground/45 whitespace-nowrap font-body" data-testid="personalised-tag">
      {fixture ? 'Personalised from test reels' : 'Personalised from your reels'}
    </span>
  )
}

function ReelLine({ r }: { r: NonNullable<CreatorDnaBlock['citedReels']>[number] }) {
  return (
    <li className="py-2.5" data-testid="cited-reel">
      <p className="text-[13px] font-body"><b>Reel {r.n}</b>{r.vsUsual ? <> · <span className={r.vsUsual.includes('above') ? 'text-green-700 font-bold' : r.vsUsual.includes('below') ? 'text-amber-700 font-bold' : ''}>{r.vsUsual}</span></> : ' · no reach data yet'}{r.watchThrough != null ? ` · watch-through ${Math.round(r.watchThrough * 100)}%` : ''}</p>
      <p className="text-[12px] text-foreground/55 font-body" style={{ fontFamily: EMOJI }}>
        {r.hookWords ? <>Opens with &ldquo;{r.hookWords}&rdquo;</> : r.partial ? 'Opening words unknown (no video file from Instagram)' : 'Opening words unknown'}
      </p>
    </li>
  )
}

export function CreatorDnaCard({ block }: { block: CreatorDnaBlock }) {
  const cited = block.citedReels || []
  const strong = cited.filter(c => c.group === 'strongest'), weak = cited.filter(c => c.group === 'weakest')
  return (
    <div className="rounded-2xl border border-border bg-background p-5 mb-5 text-foreground" data-testid="creator-dna-card">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h2 className="font-display text-[17px] font-black tracking-tight">How this compares to your last {block.sampleSize} reels</h2>
        <PersonalisedTag fixture={block.isFixture} />
      </div>
      <p className="text-[12px] text-foreground/45 font-body mt-1 mb-3 leading-relaxed">
        This video against your {block.topReels.length} strongest and {block.bottomReels.length} weakest reels, by your own Instagram numbers (&ldquo;usual&rdquo; is the median of your imported reels).
        A pattern in a small sample, not proof.{block.isFixture ? ' TEST DATA from the development fixture.' : ''}
      </p>
      {!!block.partialReels?.length && (
        <p className="text-[12px] font-body mb-3 leading-snug" data-testid="partial-line">
          {block.partialReels.map(n => `Reel ${n}`).join(', ')} {block.partialReels.length === 1 ? 'was' : 'were'} read from the thumbnail and caption only (no video file from Instagram), so {block.partialReels.length === 1 ? 'it is' : 'they are'} not counted for hook, format, pacing or watch-through.
        </p>
      )}

      <div className="rounded-xl border border-border px-3.5 py-3 mb-2" data-testid="new-hook-words">
        <p className={LABEL}>This video&rsquo;s first words</p>
        <p className="text-[13px] font-body mt-0.5" style={{ fontFamily: EMOJI }}>{block.newHookWords ? <>&ldquo;{block.newHookWords}&rdquo;</> : 'No speech found in the first seconds.'}</p>
      </div>

      {strong.length > 0 && (
        <div className="mt-3"><p className={LABEL}>Your strongest reels</p><ul className="divide-y divide-border">{strong.map(r => <ReelLine key={r.n} r={r} />)}</ul></div>
      )}
      {weak.length > 0 && (
        <div className="mt-2"><p className={LABEL}>Your weakest reels</p><ul className="divide-y divide-border">{weak.map(r => <ReelLine key={r.n} r={r} />)}</ul></div>
      )}

      <div className="mt-4">
        <p className={LABEL}>Style match</p>
        {block.comparison.length === 0 ? (
          <p className="text-[14px] text-foreground/60 font-body mt-1">Not enough fingerprint data to compare this video.</p>
        ) : block.comparison.map(c => (
          <div key={c.field} className="border-t border-border py-2.5 first:border-t-0">
            <div className="flex justify-between gap-2 flex-wrap items-center">
              <span className="text-[14px] font-body"><b>{c.label}:</b> {val(c.value)}</span>
              <span className={`text-[9px] font-bold tracking-[0.12em] uppercase px-2 py-0.5 border rounded-full whitespace-nowrap font-body ${c.verdict === 'neutral' ? 'text-foreground/40 border-border' : 'text-foreground border-foreground/60'}`}>{VERDICT[c.verdict]}</span>
            </div>
            <p className="text-[12px] text-foreground/50 font-body mt-0.5">
              {c.top_total ? `${c.top_match} of your ${c.top_total} strongest reels${c.top_reels.length ? ` (Reel${c.top_reels.length > 1 ? 's' : ''} ${c.top_reels.join(', ')})` : ''}` : 'No strongest reel to compare'}
              {' · '}
              {c.bottom_total ? `${c.bottom_match} of your ${c.bottom_total} weakest${c.bottom_reels.length ? ` (Reel${c.bottom_reels.length > 1 ? 's' : ''} ${c.bottom_reels.join(', ')})` : ''}` : 'no weakest reel to compare'}
            </p>
          </div>
        ))}
      </div>
      {block.grounding && block.grounding.checked > 0 && (
        <p className="text-[11px] text-foreground/40 font-body mt-3" data-testid="grounding-line">
          The advice on this page that mentions your reels was checked against them: {block.grounding.checked} sentence{block.grounding.checked === 1 ? '' : 's'} checked
          {block.grounding.rewritten ? `, ${block.grounding.rewritten} rewritten` : ''}{block.grounding.dropped ? `, ${block.grounding.dropped} removed` : ''}.
        </p>
      )}
      <p className="text-[12px] mt-3 font-body"><Link to="/creator-dna" className="font-bold underline underline-offset-2 text-foreground/70 hover:text-foreground">Open your reels and Creator DNA</Link></p>
    </div>
  )
}
