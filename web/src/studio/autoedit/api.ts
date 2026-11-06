// Built during the Meta Global AI Developer Hackathon (new work).
// Auto-edit client: the types of the status reply and one hook (`useAutoEdit`) used by the /edit page and the compact card on the
// results page. Every request goes through authFetch from '@/lib/auth', so an expired access token is refreshed instead of the feature
// silently disappearing.
import { useCallback, useEffect, useRef, useState } from 'react'
import { apiUrl } from '@/lib/api'
import { authFetch, getToken } from '@/lib/auth'

export type Change = { params?: { start?: number; end?: number; at?: number; role?: string; text?: string; anchor?: string; word?: string; scale?: number; duration?: number; target_lufs?: number; effect?: string; kind?: string }; id: string; type: string; group: string; label: string; reason: string; target_parameter: string; enabled: boolean; verdict: string; verdict_reasons: string[]; user_override: boolean; risk?: string; finding?: string; effect_options?: string[] }
export type Metrics = { duration_s?: number; lufs?: number | null; true_peak_db?: number | null; silence_ratio?: number; cut_count?: number; avg_shot_s?: number; black_frames?: number; frozen_frames?: number }
export type Group = { label: string; status: string; risk?: string; reasons: string[]; target: string; critic?: { verdict: string; reasons: string[]; defects: { flag: string; severity: string; detail?: string }[] } | null; metrics_after?: Metrics; objective?: { checks: { name: string; ok: boolean; detail: string; warn?: boolean }[]; ok: boolean } }
export type Tip = { step_id?: string; parameter: string; tip: string; why_manual?: string }
export type State = {
  status: string; phase: string; preset: string; presets: string[]; running: boolean; error?: string | null; trace: { t: string; kind: string; msg: string }[]
  changes: Change[]; groups: Record<string, Group>; manual_tips: Tip[]; revisions: { text: string; summary: string[] }[]; revisions_left: number
  baseline?: { metrics: Metrics } | null
  final?: { metrics: Metrics; overall_scores?: { before: Record<string, number>; after: Record<string, number>; notes?: string } | null; partial?: boolean; note?: string; not_rescored?: boolean; objective_checks?: { group: string; name: string; ok: boolean; detail: string }[]; has_captions?: boolean } | null
  cost?: { total_usd: number }
  packs?: Record<string, { label: string; headline: string; caption: string; accent: string; animation: string; for: string }>; intensities?: string[]
  style?: { pack?: string | null; font_headline?: string | null; font_caption?: string | null; animation?: string | null; intensity?: string; sfx?: boolean; pack_source?: string }
  style_choice?: { pack: string; pack_reason: string; fonts: { family: string; role: string; reason: string }[]; emphasis_count: number; notes: string[] } | null
  style_notes?: string[]
  decisions?: { area: string; text: string }[]; critic?: { mode?: string; verdict?: string; notes?: string } | null
  timing?: { total_s?: number; stages?: Record<string, number> }
  clean_cut?: { running?: boolean; ready?: boolean; ops?: number; error?: string | null }
}
export type Resp = { eligible: boolean; reason: string; state: State | null }

/** How many silent gaps the edit cut out (the kept `auto_silence` cuts). */
export const pausesRemoved = (st: State | null | undefined) => keptChanges(st).filter(c => c.type === 'cut_segment' && c.id.startsWith('auto_silence')).length
export const keptChanges = (st: State | null | undefined) => (st?.changes || []).filter(c => c.enabled && c.verdict === 'kept')

export function useAutoEdit(analysisId?: string, pollMs = 2500) {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [data, setData] = useState<Resp | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [version, setVersion] = useState(0)
  const timer = useRef<number | undefined>(undefined)
  const base = `/prysm/auto-edit/${analysisId}`

  const load = useCallback(async () => {
    if (!analysisId) return
    try {
      const r = await authFetch(apiUrl(`${base}/status`))
      if (r.ok) setData(await r.json())
    } catch { /* the next poll tries again */ }
  }, [analysisId, base])

  useEffect(() => {
    if (!analysisId || !getToken()) { setEnabled(false); return }
    authFetch(apiUrl('/prysm/auto-edit/available')).then(r => { setEnabled(r.ok); if (r.ok) load() }).catch(() => setEnabled(false))
  }, [analysisId, load])

  const running = !!data?.state?.running || !!data?.state?.clean_cut?.running
  useEffect(() => {
    window.clearInterval(timer.current)
    if (running) timer.current = window.setInterval(load, pollMs)
    return () => window.clearInterval(timer.current)
  }, [running, load, pollMs])
  const wasRunning = useRef(false)
  useEffect(() => { if (wasRunning.current && !running) setVersion(v => v + 1); wasRunning.current = running }, [running])

  async function call(method: string, path: string, body?: unknown) {
    setErr(''); setBusy(true)
    try {
      const r = await authFetch(apiUrl(`${base}${path}`), { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) { setErr(typeof j.detail === 'string' ? j.detail : 'Something went wrong.'); return false }
      if (method !== 'DELETE') setData(d => ({ eligible: true, reason: '', ...(d || {}), state: j }))
      return true
    } finally { setBusy(false) }
  }

  async function download(kind: string, name: string) {
    const r = await authFetch(apiUrl(`${base}/download/${kind}`))
    if (!r.ok) { setErr('That file is not available for this edit.'); return }
    const url = URL.createObjectURL(await r.blob())
    const a = document.createElement('a'); a.href = url; a.download = name; a.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
  }

  return { enabled, data, setData, err, setErr, busy, version, base, load, call, download }
}

/** A downloaded file as an object URL (videos and images need the Bearer header, so a plain src cannot be used). */
export function useBlobUrl(path: string | null, version = 0): { url: string | null; failed: boolean } {
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    if (!path) { setUrl(null); return }
    let dead = false, made: string | null = null
    setUrl(null); setFailed(false)
    authFetch(apiUrl(path)).then(r => (r.ok ? r.blob() : null)).then(b => {
      if (dead) return
      if (b) { made = URL.createObjectURL(b); setUrl(made) } else setFailed(true)
    }).catch(() => { if (!dead) setFailed(true) })
    return () => { dead = true; if (made) URL.revokeObjectURL(made) }
  }, [path, version])
  return { url, failed }
}
