// Built during the Meta Global AI Developer Hackathon (new work).
// Small display helpers for the Creator DNA page: text cut at a word boundary without splitting emoji, relative time, numbers.

const SEG: any = typeof Intl !== 'undefined' && (Intl as any).Segmenter ? new (Intl as any).Segmenter(undefined, { granularity: 'grapheme' }) : null

/** Characters as a user sees them (an emoji, with its joiners and skin tones, is ONE item, so cutting never breaks it). */
export function graphemes(s: string): string[] {
  return SEG ? Array.from(SEG.segment(s), (x: any) => x.segment as string) : Array.from(s)
}

/** The first `max` visible characters, cut at a word boundary, with an ellipsis. `cut` says whether anything was left out. */
export function snippet(text: string | null | undefined, max = 120): { text: string; cut: boolean } {
  const flat = (text || '').replace(/\s+/g, ' ').trim()
  if (!flat) return { text: '', cut: false }
  const g = graphemes(flat)
  if (g.length <= max) return { text: flat, cut: false }
  let head = g.slice(0, max)
  const lastSpace = head.lastIndexOf(' ')
  if (lastSpace > max * 0.5) head = head.slice(0, lastSpace)      // back up to the end of the previous word
  return { text: head.join('').replace(/[\s,.;:–—-]+$/, '') + '…', cut: true }
}

export const EMOJI_FONTS = "'Manrope', 'Apple Color Emoji', 'Segoe UI Emoji', 'Noto Color Emoji', sans-serif"

export function timeAgo(iso?: string | null, now = Date.now()): string {
  if (!iso) return 'never'
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000))
  if (s < 60) return 'just now'
  const m = Math.round(s / 60)
  if (m < 60) return `${m} minute${m === 1 ? '' : 's'} ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h} hour${h === 1 ? '' : 's'} ago`
  const d = Math.round(h / 24)
  return `${d} day${d === 1 ? '' : 's'} ago`
}

export const date = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '')
export const num = (v: number | null | undefined) => (v == null ? null : Math.round(v).toLocaleString('en-US'))
export const pct = (v: number | null | undefined) => (v == null ? null : `${Math.round(v * 100)}%`)
/** Instagram reports average watch time in milliseconds. */
export const seconds = (ms: number | null | undefined) => (ms == null ? null : `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)} s`)
/** Instagram reports the skip rate as a percent (59.1 means 59.1%). */
export const skip = (v: number | null | undefined) => (v == null ? null : `${Math.round(v)}%`)
export const words = (s: string) => s.replace(/_/g, ' ')
export const known = (s: string | null | undefined) => !!s && s !== 'unknown'

/** Rough time for an import: about 5 seconds a reel (three are read at once) plus a fixed start and the summary. */
export function estimate(n: number): string {
  const s = 12 + Math.round(n * 4.5)
  if (s < 60) return `about ${Math.max(10, Math.round(s / 5) * 5)} seconds`
  const m = Math.round(s / 30) / 2
  return `about ${m} minute${m === 1 ? '' : 's'}`
}

export const METRIC_ROWS: { key: string; label: string; fmt: (v: number) => string }[] = [
  { key: 'views', label: 'Views', fmt: v => num(v)! },
  { key: 'reach', label: 'Reach', fmt: v => num(v)! },
  { key: 'likes', label: 'Likes', fmt: v => num(v)! },
  { key: 'comments', label: 'Comments', fmt: v => num(v)! },
  { key: 'saved', label: 'Saves', fmt: v => num(v)! },
  { key: 'shares', label: 'Shares', fmt: v => num(v)! },
  { key: 'ig_reels_avg_watch_time', label: 'Avg watch time', fmt: v => seconds(v)! },
  { key: 'reels_skip_rate', label: 'Skip rate', fmt: v => skip(v)! },
]
