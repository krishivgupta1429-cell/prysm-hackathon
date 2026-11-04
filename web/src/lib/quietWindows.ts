// Built during the Meta Global AI Developer Hackathon (new work).
// ONE source for "where does the sound dip" on the results page. The left Audio Energy panel and the Viewer behaviour strip used to
// disagree: the panel flagged any 2 s stretch under 40% of a min-max scaled loudness curve (one loud peak makes everything else look
// "low"), the strip flagged stretches of 1 s or more that sit 8 dB under the video's median loudness. They gave different windows, so
// the panel said "low energy at 19-23 s" while the strip said "none found". Both now read the strip's windows (decibels under the
// median, computed once on the server); the old per-second list is used only when the strip has no audio (older analyses).
import type { ViewerAttention } from '@/studio/AttentionStrip'

export type Win = { start: number; end: number }

const t = (s: number) => (Number.isInteger(s) ? String(s) : s.toFixed(1))
export const fmtWin = (w: Win) => `${t(w.start)}-${t(w.end)}s`

export function joinList(items: string[]): string {
  if (items.length <= 1) return items.join('')
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`
}

/** The quiet windows to show: the strip's when it has audio, else the legacy list, else none. */
export function quietWindows(attention?: ViewerAttention | null, legacy?: Win[] | null): { wins: Win[]; source: 'strip' | 'legacy' | 'none' } {
  if (attention && attention.audio_energy) return { wins: attention.loudness_dips || [], source: 'strip' }
  if (legacy && legacy.length) return { wins: legacy, source: 'legacy' }
  return { wins: [], source: 'none' }
}

/** One plain sentence about this video's rhythm. Estimate wording: "may". */
export function rhythmSentence(a: ViewerAttention): string {
  const quiet = a.audio_energy ? a.loudness_dips || [] : null
  const still = a.visual_change ? a.static_stretches || [] : null
  const parts: string[] = []
  if (quiet && quiet.length) parts.push(`Sound dips at ${joinList(quiet.slice(0, 3).map(fmtWin))}, where viewers may drop off`)
  if (still && still.length) parts.push(`${parts.length ? 'the picture' : 'The picture'} barely changes at ${joinList(still.slice(0, 3).map(fmtWin))}`)
  if (parts.length) return parts.join('; ') + '.'
  if (quiet === null && still === null) return 'The sound and the picture could not be read for this video, so no rhythm estimate is shown.'
  return 'No long quiet or still stretch found, so nothing here points to a likely drop-off.'
}
