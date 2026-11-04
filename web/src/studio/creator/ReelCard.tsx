// Built during the Meta Global AI Developer Hackathon (new work).
// One imported reel: thumbnail, date, caption (cut at a word, "Show full caption"), how it did compared with the creator's usual,
// the real metrics Instagram returned ("no data yet" when empty, never 0), hook words, and the "not fully analysed" badge.
import { useState } from 'react'
import type { Reel } from './types'
import { EMOJI_FONTS, METRIC_ROWS, date, known, pct, snippet, words } from './format'

const LABEL = 'text-[10px] font-bold tracking-widest uppercase text-foreground/30 font-body'
const CHIP = 'text-[11px] font-body border border-border rounded-full px-2 py-0.5 text-foreground/60 whitespace-nowrap'

function Thumb({ reel, fixture }: { reel: Reel; fixture: boolean }) {
  const [broken, setBroken] = useState(false)
  const src = reel.thumbnailUrl
  return (
    <div className="shrink-0 w-[68px] h-[104px] rounded-lg overflow-hidden border border-border bg-muted flex items-center justify-center">
      {src && !broken ? (
        <img src={src} alt={`Thumbnail of reel ${reel.n}`} className="w-full h-full object-cover" loading="lazy" referrerPolicy="no-referrer"
          onError={() => setBroken(true)} />
      ) : (
        <span className="text-[10px] text-foreground/35 text-center px-1 font-body">{fixture ? 'test image' : 'no thumbnail'}</span>
      )}
    </div>
  )
}

export function VsUsualLine({ reel }: { reel: Reel }) {
  const v = reel.vs_usual
  if (!v) return <p className="text-[13px] font-semibold text-foreground/45 font-body" data-testid="vs-usual">No reach or views data yet</p>
  const tone = v.direction === 'above' ? 'text-green-700' : v.direction === 'below' ? 'text-amber-700' : 'text-foreground/60'
  return <p className={`text-[13px] font-bold font-body ${tone}`} data-testid="vs-usual">{v.label}</p>
}

export function ReelCard({ reel, fixture, detail = false, onOpen }: { reel: Reel; fixture: boolean; detail?: boolean; onOpen?: (n: number) => void }) {
  const [full, setFull] = useState(false)
  const caption = reel.caption || ''
  const sn = snippet(caption, detail ? 220 : 110)
  const showFull = full || (detail && !sn.cut)
  const ins = reel.insights || {}
  const noInsights = METRIC_ROWS.every(m => ins[m.key] == null)
  const hook = reel.hook_words
  const fp = reel.fingerprint
  const chips = fp ? [known(fp.hook_type) && words(fp.hook_type), known(fp.format) && words(fp.format), known(fp.pacing) && `${fp.pacing} pace`,
    fp.on_screen_text ? 'on-screen text' : 'no on-screen text', fp.topic].filter(Boolean) as string[] : []

  return (
    <article className="rounded-2xl border border-border bg-background p-4" data-testid="reel-row" id={`reel-${reel.n}`} aria-label={`Reel ${reel.n}`}>
      <div className="flex gap-3">
        <Thumb reel={reel} fixture={fixture} />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-2 flex-wrap">
            <h3 className="font-display text-[15px] font-black tracking-tight">
              {onOpen ? <button type="button" className="underline-offset-2 hover:underline focus-visible:underline outline-none" onClick={() => onOpen(reel.n)}>Reel {reel.n}</button> : `Reel ${reel.n}`}
              <span className="ml-2 text-[11px] font-normal text-foreground/40 font-body">{date(reel.postedAt)}{reel.duration_s ? ` · ${Math.round(reel.duration_s)} s` : ''}</span>
            </h3>
            {fixture && <span className="text-[9px] font-bold tracking-widest uppercase bg-foreground text-background rounded-full px-2 py-0.5">Fixture</span>}
          </div>
          <VsUsualLine reel={reel} />
          {reel.partial && (
            <p className="mt-1.5 inline-block text-[10px] font-bold tracking-wide uppercase border border-dashed border-foreground/60 rounded-full px-2 py-0.5 font-body" data-testid="partial-badge">
              {reel.analysis_level === 'caption_only' ? 'Caption only' : 'Thumbnail + caption only'} · not fully analysed
            </p>
          )}
        </div>
      </div>

      {caption ? (
        <div className="mt-3">
          <p className={`text-[13px] leading-relaxed text-foreground/75 ${showFull ? 'whitespace-pre-line' : ''}`} style={{ fontFamily: EMOJI_FONTS, overflowWrap: 'anywhere' }} data-testid="caption">
            {showFull ? caption : sn.text}
          </p>
          {sn.cut && (
            <button type="button" className="mt-1 text-[12px] font-bold text-foreground/60 hover:text-foreground underline underline-offset-2 font-body" aria-expanded={showFull}
              onClick={() => setFull(f => !f)} data-testid="caption-toggle">{showFull ? 'Show less' : 'Show full caption'}</button>
          )}
        </div>
      ) : <p className="mt-3 text-[13px] text-foreground/40 font-body">No caption</p>}

      <p className="mt-3 text-[12px] font-body text-foreground/60" data-testid="hook-words">
        <span className="font-bold text-foreground/70">Hook words: </span>
        {hook ? <q style={{ fontFamily: EMOJI_FONTS }}>{hook}</q> : reel.hook_words_status === 'no_speech' ? 'no speech in the first seconds' : 'unknown'}
        {!hook && reel.hook_words_status !== 'no_speech' && reel.partial ? ' (no video file from Instagram)' : ''}
      </p>

      <div className="mt-3">
        <p className={LABEL}>What Instagram measured</p>
        {noInsights ? (
          <p className="text-[12px] text-foreground/50 font-body mt-1" data-testid="no-insights">No data yet. Instagram can take up to 48 hours to serve the numbers for a new reel.</p>
        ) : (
          <dl className="mt-1 grid grid-cols-2 sm:grid-cols-4 gap-x-3 gap-y-2" data-testid="metrics">
            {METRIC_ROWS.map(m => {
              const v = ins[m.key]
              return (
                <div key={m.key}>
                  <dt className="text-[10px] text-foreground/40 font-body">{m.label}</dt>
                  <dd className={`text-[13px] font-body ${v == null ? 'text-foreground/35' : 'font-bold text-foreground/80'}`}>{v == null ? 'no data yet' : m.fmt(v)}</dd>
                </div>
              )
            })}
            <div className="col-span-2">
              <dt className="text-[10px] text-foreground/40 font-body">Watch-through</dt>
              <dd className={`text-[13px] font-body ${reel.watch_through == null ? 'text-foreground/35' : 'font-bold text-foreground/80'}`}>
                {reel.watch_through != null ? pct(reel.watch_through) : reel.partial ? 'none (no video)' : 'no data yet'}
              </dd>
            </div>
          </dl>
        )}
      </div>

      {chips.length > 0 && <div className="mt-3 flex flex-wrap gap-1.5">{chips.map((t, i) => <span key={i} className={CHIP}>{t}</span>)}</div>}
      {reel.partial && reel.partial_reason ? <p className="mt-2 text-[12px] text-foreground/70 font-body" data-testid="partial-reason">{reel.partial_reason}</p>
        : reel.note ? <p className="mt-2 text-[12px] text-foreground/45 font-body">{reel.note}</p> : null}
      {reel.permalink && !fixture && <a href={reel.permalink} target="_blank" rel="noreferrer" className="mt-2 inline-block text-[12px] font-bold text-foreground/60 hover:text-foreground underline underline-offset-2 font-body">Open on Instagram</a>}
    </article>
  )
}
