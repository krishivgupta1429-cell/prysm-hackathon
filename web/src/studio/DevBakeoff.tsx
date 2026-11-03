// Built during the Meta Global AI Developer Hackathon (new work).
// Local-only review page (route /dev/bakeoff, dev server with VITE_DEV_BAKEOFF=1): the saved blind bake-off variants, one per letter,
// rendered by the real results page. Nothing about the model, cost, time or tokens is on screen or in the data until "Reveal key".
import { useEffect, useMemo, useState } from 'react'
import { apiUrl } from '@/lib/api'
import Results from '@/pages/Results'

type Variants = Record<string, Record<string, unknown>>
type KeyRow = { variant: string; model: string; style: string; cost_usd: number; latency_s: number; tokens_in: number; tokens_out: number; reasoning_tokens: number }
type Key = Record<string, Record<string, KeyRow>>

const VIDEOS = ['dance', 'reel', 'saree']
const LETTERS = ['X', 'Y', 'Z']
const PICKS_KEY = 'bakeoff_picks'

function loadPicks(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(PICKS_KEY) || '{}') } catch { return {} }
}

const btn = (active: boolean) =>
  `px-4 py-2 text-[13px] font-body font-semibold border rounded-full transition-colors ${active ? 'bg-foreground text-background border-foreground' : 'bg-background text-foreground border-foreground/30 hover:border-foreground'}`

export default function DevBakeoff() {
  const [data, setData] = useState<Record<string, { variants: Variants[string] }> | null>(null)
  const [error, setError] = useState('')
  const [video, setVideo] = useState(VIDEOS[0])
  const [letter, setLetter] = useState(LETTERS[0])
  const [picks, setPicks] = useState<Record<string, string>>(loadPicks)
  const [key, setKey] = useState<Key | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    fetch(apiUrl('/dev/bakeoff'))
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(d => setData(d.videos))
      .catch(e => setError(`Could not load the saved variants (${e.message}). Is the backend running with DEV_BAKEOFF=1?`))
  }, [])

  const savePick = (v: string, l: string) => {
    const next = { ...picks, [v]: picks[v] === l ? '' : l }
    setPicks(next)
    try { localStorage.setItem(PICKS_KEY, JSON.stringify(next)) } catch { /* private mode: the pick just is not remembered */ }
  }

  const reveal = () =>
    fetch(apiUrl('/dev/bakeoff/reveal'))
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(d => setKey(d.key))
      .catch(e => setError(`Could not load the key (${e.message}).`))

  const summary = useMemo(() => {
    const lines = ['Bake-off picks']
    for (const v of VIDEOS) {
      const p = picks[v]
      const k = key?.[v]?.[p]
      lines.push(`${v}: ${p || 'no pick'}${k ? ` = ${k.variant} (${k.model}, ${k.style}, $${k.cost_usd.toFixed(4)}, ${k.latency_s}s)` : ''}`)
    }
    return lines.join('\n')
  }, [picks, key])

  const copy = () => navigator.clipboard.writeText(summary).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1800) })

  if (error) return <div className="min-h-screen bg-background text-foreground p-6 font-body">{error}</div>
  if (!data) return <div className="min-h-screen bg-background text-foreground p-6 font-body">Loading...</div>
  const current = data[video]?.variants?.[letter]

  return (
    <div className="bg-background text-foreground">
      <div className="sticky top-0 z-[200] bg-background border-b border-foreground/20 px-4 py-3 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] tracking-widest uppercase font-body text-foreground/50 mr-2">Bake-off · local only</span>
          {VIDEOS.map(v => (
            <button key={v} className={btn(v === video)} onClick={() => { setVideo(v); setLetter(LETTERS[0]) }}>
              {v}{picks[v] ? ` · ${picks[v]}` : ''}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {LETTERS.map(l => <button key={l} className={btn(l === letter)} onClick={() => setLetter(l)}>{l}</button>)}
          <button className={`${btn(picks[video] === letter)} ml-auto`} onClick={() => savePick(video, letter)}>
            {picks[video] === letter ? 'My pick ✓' : 'My pick'}
          </button>
        </div>
      </div>

      {current ? <Results key={`${video}-${letter}`} injected={current as Record<string, unknown>} /> : <p className="p-6 font-body">No saved variant for {video} {letter}.</p>}

      <div className="border-t border-foreground/20 px-4 py-8 space-y-4 max-w-3xl mx-auto font-body">
        <div className="flex flex-wrap gap-2">
          <button className={btn(false)} onClick={reveal}>Reveal key</button>
          <button className={btn(false)} onClick={copy}>{copied ? 'Copied' : 'Copy my picks'}</button>
        </div>
        <p className="text-[12px] text-foreground/50">Your picks: {VIDEOS.map(v => `${v} ${picks[v] || '–'}`).join(' · ')}</p>
        {key && (
          <div className="space-y-6" data-testid="bakeoff-key">
            {VIDEOS.map(v => (
              <div key={v}>
                <h3 className="text-[13px] font-semibold uppercase tracking-widest mb-2">{v} · my pick: {picks[v] || 'none'}</h3>
                <div className="overflow-x-auto">
                  <table className="w-full text-[12px] border border-foreground/20">
                    <thead><tr className="border-b border-foreground/20 text-left">
                      <th className="p-2">Letter</th><th className="p-2">Variant</th><th className="p-2">Model</th><th className="p-2">Style</th>
                      <th className="p-2">Cost</th><th className="p-2">Time</th><th className="p-2">Tokens in / out (reasoning)</th>
                    </tr></thead>
                    <tbody>
                      {LETTERS.map(l => {
                        const k = key[v]?.[l]
                        return k && (
                          <tr key={l} className={`border-b border-foreground/10 ${picks[v] === l ? 'bg-foreground text-background' : ''}`}>
                            <td className="p-2 font-semibold">{l}{picks[v] === l ? ' ← my pick' : ''}</td><td className="p-2">{k.variant}</td><td className="p-2">{k.model}</td><td className="p-2">{k.style}</td>
                            <td className="p-2">${k.cost_usd.toFixed(4)}</td><td className="p-2">{k.latency_s}s</td>
                            <td className="p-2">{k.tokens_in.toLocaleString()} / {k.tokens_out.toLocaleString()} ({k.reasoning_tokens.toLocaleString()})</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
