// Built during the Meta Global AI Developer Hackathon (new work).
// Shared inline styles for the Auto-edit page and the results cards. ONE card surface (`card`, same as the .p-card class in index.css):
// the page's own cream, the theme border and a 16px radius, like the parameter cards. Notes are 12px or more in a darker grey.
import type { CSSProperties } from 'react'

export const INK = '#1f1f1f'
export const INK2 = '#55524b'          // notes and labels: about 7:1 on the cream page
export const LINE = 'hsl(var(--border))'
export const card: CSSProperties = { background: 'hsl(var(--card))', border: `1px solid ${LINE}`, borderRadius: 16, color: INK }
export const box: CSSProperties = { ...card, padding: 20, marginBottom: 16 }
export const tag: CSSProperties = { fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', padding: '2px 8px', border: `1px solid ${LINE}`, borderRadius: 999, color: INK2, whiteSpace: 'nowrap' }
export const btn: CSSProperties = { background: INK, color: '#fff', border: `1px solid ${INK}`, borderRadius: 999, padding: '10px 18px', fontSize: 13, fontWeight: 600, cursor: 'pointer' }
export const btn2: CSSProperties = { ...btn, background: 'transparent', color: INK, border: `1px solid ${LINE}` }
export const h2: CSSProperties = { fontSize: 16, fontWeight: 700, margin: 0 }
export const h3: CSSProperties = { fontSize: 12, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: INK2, margin: '16px 0 8px' }
export const muted: CSSProperties = { fontSize: 12, color: INK2, lineHeight: 1.5 }
export const fmt = (n?: number | null, d = 1) => (n == null ? '–' : n.toFixed(d))
export const SCORED = ['Hook & Opening', 'Retention & Pacing', 'Shareability', 'Audio & Music', 'Trend Alignment', 'Visual Quality & Originality', 'On-Screen Text']
