// Built during the Meta Global AI Developer Hackathon (new work).
// Auto-edit changes grouped for people (about six groups instead of fifteen rows), and the "recipe" for finishing a clean cut in another
// editor: one plain line per kept overlay change with exact timestamps. Pure functions, tested without the page.
import type { Change } from './api'

export type GroupDef = { key: string; label: string; match: (c: Change) => boolean }
export const GROUP_DEFS: GroupDef[] = [
  { key: 'pauses', label: 'Pauses tightened', match: c => c.type === 'cut_segment' },
  { key: 'fillers', label: 'Filler words removed', match: c => c.type === 'remove_filler' },
  { key: 'trim', label: 'Trim and cold open', match: c => c.type === 'trim' || c.type === 'cold_open' },
  { key: 'zoom', label: 'Zoom punches', match: c => c.type === 'zoom_punch' },
  { key: 'captions', label: 'Captions', match: c => c.type === 'caption_burn' },
  { key: 'loudness', label: 'Loudness and noise', match: c => c.type === 'loudnorm' || c.type === 'music_duck' },
  { key: 'hook', label: 'Hook text', match: c => c.type === 'text_overlay' && c.params?.role === 'hook' },
  { key: 'labels', label: 'Labels', match: c => c.type === 'text_overlay' && c.params?.role !== 'hook' },
  { key: 'sound', label: 'Sound accents', match: c => c.type === 'sfx_hit' },
]

export type ChangeGroup = { key: string; label: string; items: Change[]; kept: number; reverted: number; enabled: number; risk: 'safe' | 'taste' }

export function groupChanges(changes: Change[]): ChangeGroup[] {
  const out: ChangeGroup[] = []
  const used = new Set<string>()
  for (const d of GROUP_DEFS) {
    const items = changes.filter(c => d.match(c))
    items.forEach(c => used.add(c.id))
    if (items.length) out.push(mk(d.key, d.label, items))
  }
  const rest = changes.filter(c => !used.has(c.id))
  if (rest.length) out.push(mk('other', 'Other changes', rest))
  return out
}

function mk(key: string, label: string, items: Change[]): ChangeGroup {
  return {
    key, label, items,
    kept: items.filter(c => c.verdict === 'kept').length, reverted: items.filter(c => c.verdict === 'reverted').length, enabled: items.filter(c => c.enabled).length,
    risk: items.every(c => c.risk === 'safe') ? 'safe' : 'taste',
  }
}

/** The one chip for a whole group. */
export function groupVerdict(g: ChangeGroup): 'KEPT' | 'REVERTED' | 'OFF' | 'MIXED' {
  if (g.reverted === g.items.length) return 'REVERTED'
  if (g.enabled === 0) return 'OFF'
  if (g.kept === g.items.length) return 'KEPT'
  return 'MIXED'
}

const t1 = (n: number) => (Math.round(n * 10) / 10).toFixed(1)

/** Where in the ORIGINAL video a change happens (or in the edited video, for text that is placed on the new timeline). */
export function whenText(c: Change): string {
  const p = c.params || {}
  if (c.type === 'cut_segment' || c.type === 'trim' || c.type === 'cold_open') return p.start != null && p.end != null ? `${t1(p.start)}-${t1(p.end)} s of the original` : ''
  if (c.type === 'remove_filler') return p.start != null ? `at ${t1(p.start)} s of the original` : ''
  if (c.type === 'zoom_punch' || c.type === 'sfx_hit') return p.at != null ? `at ${t1(p.at)} s of the original` : ''
  if (c.type === 'text_overlay') return p.start != null && p.end != null ? `${t1(p.start)}-${t1(p.end)} s ${p.anchor === 'output' ? 'of the edited video' : 'of the original'}` : ''
  if (c.type === 'loudnorm') return 'the whole video'
  if (c.type === 'caption_burn') return 'the whole video'
  return ''
}

/** A time in the original -> the same moment in a clean cut that has only the kept pause cuts. A time inside a cut lands on the cut's start. */
export function cleanTime(t: number, changes: Change[]): number {
  let removed = 0
  for (const c of changes) {
    if (c.type !== 'cut_segment' || !c.id.startsWith('auto_silence') || !c.enabled || c.verdict !== 'kept') continue
    const s = c.params?.start, e = c.params?.end
    if (s == null || e == null) continue
    if (t >= e) removed += e - s
    else if (t > s) removed += t - s
  }
  return Math.max(0, t - removed)
}

/** One plain line per kept overlay change, with exact times in the clean cut. */
export function recipeLines(changes: Change[]): string[] {
  const kept = changes.filter(c => c.enabled && c.verdict === 'kept')
  const out: string[] = []
  const span = (c: Change) => {
    const p = c.params || {}
    if (p.start == null || p.end == null) return ''
    const a = p.anchor === 'output' ? p.start : cleanTime(p.start, changes)
    const b = p.anchor === 'output' ? p.end : cleanTime(p.end, changes)
    return `from ${t1(a)} to ${t1(b)} s`
  }
  if (kept.some(c => c.type === 'caption_burn')) out.push("Captions: switch on Edits' own auto-captions for the whole video.")
  for (const c of kept.filter(c => c.type === 'text_overlay' && c.params?.role === 'hook')) out.push(`Hook text “${c.params?.text ?? ''}”: add a text layer ${span(c)}.`)
  for (const c of kept.filter(c => c.type === 'text_overlay' && c.params?.role !== 'hook')) out.push(`Label “${c.params?.text ?? ''}”: add a text layer ${span(c)}.`)
  for (const c of kept.filter(c => c.type === 'zoom_punch')) {
    const at = c.params?.at
    if (at == null) continue
    out.push(`Zoom punch: zoom in about ${Math.round(((c.params?.scale ?? 1.1) - 1) * 100)}% at ${t1(cleanTime(at, changes))} s for ${t1(c.params?.duration ?? 0.25)} s.`)
  }
  return out
}
