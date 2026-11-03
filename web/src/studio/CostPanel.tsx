// Built during the Meta Global AI Developer Hackathon (new work).
// Per-analysis cost breakdown. Rendered ONLY when VITE_SHOW_COST_PANEL=1 (local) or the logged-in user is the founder
// (VITE_FOUNDER_EMAIL); the backend answers 404 to anyone else, so normal users never get the numbers or any hint of them.
import { useEffect, useState } from 'react'
import { apiUrl } from '@/lib/api'
import { getToken, getUser } from '@/lib/auth'
import { FOUNDER_EMAIL, SHOW_COST_PANEL } from '@/lib/config'

type Llm = { model?: string | null; tokens_in: number; tokens_out: number; reasoning_tokens: number; cost_usd: number; calls?: number; note?: string }
type Breakdown = {
  llm?: Record<string, Llm>
  wonder_used?: boolean
  spark_1_2_audio?: Llm
  voice_transcribe?: { audio_seconds: number; cost_usd: number }
  sam_3_1?: { frames: number; cost_usd: number }
  gemini?: { audio?: { tokens_in: number; tokens_out: number; cost_usd: number }; trends?: { called: boolean; note?: string } }
  modal_gpu?: { estimate: boolean; gpu?: string | null; gpu_seconds: number; rate_usd_per_s?: number | null; cost_usd: number }
  acrcloud?: { called: boolean; matched: boolean }
  apify?: { called: boolean; cost_usd?: number | null }
  total_usd: number
}

const usd = (n?: number | null) => (n == null ? 'n/a' : `$${n.toFixed(4)}`)
const tok = (n: number) => n.toLocaleString('en-US')

export function canShowCostPanel(): boolean {
  if (SHOW_COST_PANEL) return true
  const email = (getUser()?.email || '').trim().toLowerCase()
  return !!FOUNDER_EMAIL && email === FOUNDER_EMAIL
}

export default function CostPanel({ analysisId }: { analysisId?: string }) {
  const [data, setData] = useState<Breakdown | null>(null)
  const [edit, setEdit] = useState<{ cost?: { total_usd: number; by_service?: Record<string, { usd: number; calls: number; seconds: number }> }; timing?: { total_s?: number; render_s?: number }; ops?: number; revisions?: number } | null>(null)
  const allowed = canShowCostPanel()

  useEffect(() => {
    const tk = getToken()
    if (!allowed || !analysisId || !tk) return
    fetch(apiUrl(`/prysm/dashboard/${analysisId}/cost`), { headers: { Authorization: `Bearer ${tk}` } })
      .then(r => (r.ok ? r.json() : null))
      .then(d => setData(d?.cost?.breakdown ?? null))
      .catch(() => setData(null))
    // Auto-edit cost (404 unless the flag is on and an edit exists)
    fetch(apiUrl(`/prysm/auto-edit/${analysisId}/cost`), { headers: { Authorization: `Bearer ${tk}` } })
      .then(r => (r.ok ? r.json() : null)).then(setEdit).catch(() => setEdit(null))
  }, [allowed, analysisId])

  if (!allowed || !data) return null
  const llm = data.llm || {}
  const rows: [string, string, string][] = []
  for (const k of ['vision', 'scoring', 'chat']) {
    const v = llm[k]
    if (v) rows.push([`${k[0].toUpperCase()}${k.slice(1)} · ${v.model ?? 'not used'}`, `${tok(v.tokens_in)} in / ${tok(v.tokens_out)} out (${tok(v.reasoning_tokens)} reasoning)`, usd(v.cost_usd)])
  }
  const a = data.spark_1_2_audio
  if (a && a.calls) rows.push(['Audio listener · muse-spark-1.2', `${tok(a.tokens_in)} in / ${tok(a.tokens_out)} out (${tok(a.reasoning_tokens)} reasoning)`, usd(a.cost_usd)])
  if (data.voice_transcribe) rows.push(['Voice Transcribe', `${data.voice_transcribe.audio_seconds}s of audio`, usd(data.voice_transcribe.cost_usd)])
  if (data.sam_3_1) rows.push(['SAM 3.1', `${data.sam_3_1.frames} frames`, usd(data.sam_3_1.cost_usd)])
  if (data.gemini?.audio) rows.push(['Gemini audio', `${tok(data.gemini.audio.tokens_in)} in / ${tok(data.gemini.audio.tokens_out)} out`, usd(data.gemini.audio.cost_usd)])
  if (data.gemini?.trends?.called) rows.push(['Gemini trends', 'not metered', 'n/a'])
  if (data.modal_gpu) rows.push([`Modal GPU (estimate)${data.modal_gpu.gpu ? ' · ' + data.modal_gpu.gpu : ''}`, `${data.modal_gpu.gpu_seconds}s × ${data.modal_gpu.rate_usd_per_s ?? '?'}/s`, usd(data.modal_gpu.cost_usd)])
  if (data.acrcloud) rows.push(['ACRCloud', data.acrcloud.called ? (data.acrcloud.matched ? 'called · matched' : 'called · no match') : 'not called', 'per-call'])
  if (edit?.cost) {
    for (const [svc, v] of Object.entries(edit.cost.by_service || {})) rows.push([`Auto-edit · ${svc}`, `${v.calls} call${v.calls === 1 ? '' : 's'} · ${Math.round(v.seconds)}s`, usd(v.usd)])
    rows.push(['Auto-edit total', `${edit.ops ?? 0} changes · ${edit.revisions ?? 0} revisions · ${Math.round(edit.timing?.total_s ?? 0)}s`, usd(edit.cost.total_usd)])
  }
  if (data.apify) rows.push(['Apify', data.apify.called ? 'called' : 'not called', data.apify.called ? usd(data.apify.cost_usd) : '-'])

  return (
    <details className="border border-border rounded-2xl px-5 py-3 mb-5" data-testid="cost-panel">
      <summary className="cursor-pointer text-[12px] font-semibold tracking-widest uppercase text-foreground/40 font-body">
        Cost breakdown · {usd(data.total_usd)}
      </summary>
      <ul className="mt-3 space-y-1.5">
        {rows.map(([label, detail, cost]) => (
          <li key={label} className="flex items-baseline justify-between gap-4 text-[12px] font-body">
            <span className="text-foreground/50">{label}</span>
            <span className="text-foreground/35 text-right">{detail} · {cost}</span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-[11px] text-foreground/30 font-body">Reasoning tokens are billed as output tokens. Modal GPU is an estimate; ACRCloud and Apify are per-call fees.</p>
    </details>
  )
}
