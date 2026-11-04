// Built during the Meta Global AI Developer Hackathon (new work).
// "Did my fix work?": link a Prysm analysis to the reel you posted after it, tick the advice you applied, and see that reel's real
// numbers against your own usual. Honest wording: better / about the same / worse than your usual, never "it worked".
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiUrl } from '@/lib/api'
import { authFetch } from '@/lib/auth'
import { Button } from '@/components/ui/button'
import { date } from './format'
import type { AnalysisItem, FixLink, Reel, ResultRow } from './types'

const LABEL = 'text-[10px] font-bold tracking-widest uppercase text-foreground/30 font-body'
const SELECT = 'w-full h-10 rounded-lg border border-input bg-background px-3 text-[14px] font-body focus-visible:ring-2 focus-visible:ring-ring outline-none'

const DIR: Record<ResultRow['direction'], { text: string; cls: string }> = {
  better: { text: 'Better than your usual', cls: 'border-green-700/40 text-green-800 bg-green-50' },
  worse: { text: 'Worse than your usual', cls: 'border-amber-700/40 text-amber-800 bg-amber-50' },
  same: { text: 'About your usual', cls: 'border-border text-foreground/60' },
  none: { text: 'No comparison yet', cls: 'border-dashed border-border text-foreground/40' },
}

function ResultCard({ link, onOpen, onRemove }: { link: FixLink; onOpen: (n: number) => void; onRemove: (id: string) => void }) {
  const r = link.result
  return (
    <article className="rounded-2xl border border-border bg-background p-4 sm:p-5" data-testid="fix-link" aria-label="Analysis linked to a reel">
      <div className="flex items-start justify-between gap-2 flex-wrap">
        <div className="min-w-0">
          <p className={LABEL}>Prysm analysis{link.analysis.createdAt ? ` · ${date(link.analysis.createdAt)}` : ''}{link.analysis.score != null ? ` · score ${link.analysis.score}` : ''}</p>
          <h3 className="font-display text-[17px] font-black tracking-tight">
            {link.reel ? <>posted as <button type="button" className="underline underline-offset-2 hover:no-underline" onClick={() => onOpen(link.reel!.n)}>Reel {link.reel.n}</button>{link.reel.postedAt ? ` on ${date(link.reel.postedAt)}` : ''}</> : 'Linked reel is not in your current import'}
          </h3>
          {link.analysis.summary && <p className="mt-1 text-[12px] text-foreground/50 font-body">{link.analysis.summary}</p>}
        </div>
        <Button variant="outline" size="sm" onClick={() => onRemove(link.id)} aria-label="Remove this link" data-testid="fix-unlink">Remove link</Button>
      </div>

      {link.applied.length > 0 || link.usedAutoEdit ? (
        <div className="mt-3" data-testid="fix-applied">
          <p className={LABEL}>Advice you applied</p>
          <ul className="mt-1 list-disc pl-5 space-y-0.5 text-[13px] text-foreground/75 font-body">
            {link.usedAutoEdit && <li>You posted Prysm&rsquo;s auto-edited version</li>}
            {link.applied.map((a, i) => <li key={i}>{a}</li>)}
          </ul>
        </div>
      ) : <p className="mt-3 text-[12px] text-foreground/45 font-body">You did not tick any advice as applied.</p>}

      {!r ? <p className="mt-3 text-[13px] text-foreground/55 font-body">Re-import your reels so this reel is included, then its numbers appear here.</p> : (
        <div className="mt-4" data-testid="fix-result">
          <p className="text-[13px] font-bold font-body" data-testid="fix-headline">
            {r.compared === 0 ? 'No numbers to compare yet.' : `${r.compared} number${r.compared === 1 ? '' : 's'} compared with your other ${r.basedOn} reel${r.basedOn === 1 ? '' : 's'}: ${r.better} better, ${r.same} about the same, ${r.worse} worse.`}
          </p>
          <ul className="mt-2 divide-y divide-border rounded-xl border border-border">
            {r.rows.map(row => (
              <li key={row.key} className="flex items-center justify-between gap-3 flex-wrap px-3 py-2.5" data-testid="fix-row">
                <div className="min-w-0">
                  <p className="text-[13px] font-bold font-body">{row.plainText || row.label}</p>
                  <p className="text-[12px] text-foreground/50 font-body">
                    {row.plainText ? `${row.label}: ` : ''}{row.valueText ? `this reel ${row.valueText}` : ''}{row.usualText ? ` · your usual ${row.usualText}` : ''}{row.note ? `${row.valueText ? ' · ' : ''}${row.note}` : ''}
                  </p>
                </div>
                <span className={`text-[11px] font-bold font-body border rounded-full px-2 py-0.5 whitespace-nowrap ${DIR[row.direction].cls}`}>{row.direction === 'none' && row.note === 'no data yet' ? 'No data yet' : DIR[row.direction].text}</span>
              </li>
            ))}
          </ul>
          <ul className="mt-3 space-y-1 text-[12px] text-foreground/50 font-body" data-testid="fix-notes">
            {r.notes.map((n, i) => <li key={i}>{n}</li>)}
          </ul>
        </div>
      )}
    </article>
  )
}

export function FixLoop({ reels, reloadKey, onOpen }: { reels: Reel[]; reloadKey: number; onOpen: (n: number) => void }) {
  const [analyses, setAnalyses] = useState<AnalysisItem[] | null>(null)
  const [links, setLinks] = useState<FixLink[]>([])
  const [error, setError] = useState('')
  const [open, setOpen] = useState(false)
  const [aId, setAId] = useState('')
  const [mediaId, setMediaId] = useState('')
  const [applied, setApplied] = useState<string[]>([])
  const [auto, setAuto] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const [a, l] = await Promise.all([authFetch(apiUrl('/instagram/analyses')), authFetch(apiUrl('/instagram/links'))])
      if (!a.ok || !l.ok) throw new Error('load')
      setAnalyses((await a.json()).analyses); setLinks((await l.json()).links); setError('')
    } catch { setError('Could not load your analyses. Try again in a moment.') }
  }, [])
  useEffect(() => { load() }, [load, reloadKey])

  const unlinked = (analyses || []).filter(a => !a.linked)
  const chosen = (analyses || []).find(a => a.id === aId) || null
  const reelByMedia = (m: string) => reels.find(r => (r as any).media_id === m)

  function pick(id: string) {
    setAId(id); setApplied([]); setAuto(false); setError('')
    const a = (analyses || []).find(x => x.id === id)
    const likely = a?.suggestions.find(s => s.likely) || a?.suggestions[0]
    setMediaId(likely?.media_id || '')
  }
  async function save() {
    if (!chosen || !mediaId) return
    setBusy(true); setError('')
    try {
      const r = await authFetch(apiUrl('/instagram/links'), { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ analysisId: chosen.id, mediaId, applied, usedAutoEdit: auto }) })
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'Could not save the link.')
      setOpen(false); setAId(''); setMediaId(''); await load()
    } catch (e: any) { setError(e.message || 'Could not save the link.') }
    setBusy(false)
  }
  async function remove(id: string) {
    const r = await authFetch(apiUrl(`/instagram/links/${id}`), { method: 'DELETE' })
    if (r.ok) await load(); else setError('Could not remove the link.')
  }

  return (
    <section aria-labelledby="fix-h" data-testid="fix-loop" className="space-y-3">
      <div>
        <p className={LABEL}>Close the loop</p>
        <h2 id="fix-h" className="font-display text-xl font-black tracking-tight">Did my fix work?</h2>
        <p className="mt-1 text-[13px] text-foreground/55 font-body">Link a Prysm analysis to the reel you posted after it. You see that reel&rsquo;s real numbers against your own usual, next to the advice you applied. One reel is not proof, and Instagram numbers can lag up to 48 hours.</p>
      </div>
      {error && <p className="text-[13px] font-bold text-destructive font-body" role="alert" data-testid="fix-error">{error}</p>}
      {links.map(l => <ResultCard key={l.id} link={l} onOpen={onOpen} onRemove={remove} />)}

      {analyses && analyses.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border p-5 text-center" data-testid="fix-empty-analyses">
          <p className="text-[13px] text-foreground/55 font-body">You have no saved analyses yet. <Link to="/analyse" className="font-bold underline underline-offset-2">Analyse a video</Link>, post it, re-import your reels, then link them here.</p>
        </div>
      ) : analyses && unlinked.length === 0 && links.length > 0 ? null : (
        !open ? (
          <Button variant="outline" onClick={() => setOpen(true)} disabled={!analyses || reels.length === 0} data-testid="fix-open">Link an analysis to a posted reel</Button>
        ) : (
          <div className="rounded-2xl border border-border bg-background p-4 sm:p-5 space-y-4" data-testid="fix-form">
            <div>
              <label htmlFor="fix-analysis" className={LABEL}>Prysm analysis</label>
              <select id="fix-analysis" className={`${SELECT} mt-1`} value={aId} onChange={e => pick(e.target.value)} data-testid="fix-analysis">
                <option value="">Choose an analysis…</option>
                {unlinked.map(a => <option key={a.id} value={a.id}>{date(a.createdAt)} · score {a.score ?? '?'}{a.durationS ? ` · ${Math.round(a.durationS)} s` : ''} · {a.summary.slice(0, 50)}</option>)}
              </select>
            </div>
            {chosen && (
              <>
                <div>
                  <label htmlFor="fix-reel" className={LABEL}>The reel you posted</label>
                  <select id="fix-reel" className={`${SELECT} mt-1`} value={mediaId} onChange={e => setMediaId(e.target.value)} data-testid="fix-reel">
                    <option value="">Choose a reel…</option>
                    {reels.map(r => {
                      const s = chosen.suggestions.find(x => x.media_id === (r as any).media_id)
                      return <option key={r.id} value={(r as any).media_id}>{`Reel ${r.n} · ${date(r.postedAt)}${r.duration_s ? ` · ${Math.round(r.duration_s)} s` : ''}${s?.likely ? ' · likely match' : ''}`}</option>
                    })}
                  </select>
                  {mediaId && (() => { const s = chosen.suggestions.find(x => x.media_id === mediaId); return s ? <p className="mt-1 text-[12px] text-foreground/50 font-body" data-testid="fix-why">{s.likely ? 'Likely match: ' : 'Why it might be this one: '}{s.reasons.join('; ')}.</p> : null })()}
                  {chosen.suggestions.length === 0 && <p className="mt-1 text-[12px] text-foreground/50 font-body">No reel in your import was posted after this analysis. Post it, then re-import your reels.</p>}
                </div>
                <fieldset>
                  <legend className={LABEL}>Which advice did you apply?</legend>
                  <div className="mt-2 space-y-2">
                    {chosen.advice.map(a => (
                      <label key={a.id} className="flex items-start gap-2 text-[13px] font-body text-foreground/75 cursor-pointer">
                        <input type="checkbox" className="mt-1 h-4 w-4 accent-foreground" checked={applied.includes(a.text)} onChange={e => setApplied(p => e.target.checked ? [...p, a.text] : p.filter(x => x !== a.text))} />
                        <span>{a.text}</span>
                      </label>
                    ))}
                    {chosen.hasEditedVideo && (
                      <label className="flex items-start gap-2 text-[13px] font-body text-foreground/75 cursor-pointer">
                        <input type="checkbox" className="mt-1 h-4 w-4 accent-foreground" checked={auto} onChange={e => setAuto(e.target.checked)} />
                        <span>I posted Prysm&rsquo;s auto-edited version</span>
                      </label>
                    )}
                    {chosen.advice.length === 0 && !chosen.hasEditedVideo && <p className="text-[12px] text-foreground/45 font-body">This analysis has no advice items to tick.</p>}
                  </div>
                </fieldset>
              </>
            )}
            <div className="flex gap-2 flex-wrap">
              <Button onClick={save} disabled={busy || !chosen || !mediaId} data-testid="fix-save">{busy ? 'Saving…' : 'Save link'}</Button>
              <Button variant="outline" onClick={() => { setOpen(false); setAId('') }}>Cancel</Button>
            </div>
          </div>
        )
      )}
    </section>
  )
}
