// Built during the Meta Global AI Developer Hackathon (new work).
// Shared inline styles for the Auto-edit studio: black and white on the page's own cream background.
import type { CSSProperties } from 'react'

export const box: CSSProperties = { border: '1px solid #e5e5e5', borderRadius: 16, padding: 20, background: '#fff', color: '#0a0a0a', marginBottom: 16 }
export const tag: CSSProperties = { fontSize: 9, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', padding: '2px 8px', border: '1px solid #d4d4d4', borderRadius: 999, color: '#737373', whiteSpace: 'nowrap' }
export const btn: CSSProperties = { background: '#0a0a0a', color: '#fff', border: '1px solid #0a0a0a', borderRadius: 999, padding: '10px 18px', fontSize: 13, fontWeight: 600, cursor: 'pointer' }
export const btn2: CSSProperties = { ...btn, background: '#fff', color: '#0a0a0a', border: '1px solid #d4d4d4' }
export const h2: CSSProperties = { fontSize: 16, fontWeight: 700, margin: 0 }
export const h3: CSSProperties = { fontSize: 11, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: '#737373', margin: '16px 0 8px' }
export const muted: CSSProperties = { fontSize: 12, color: '#737373', lineHeight: 1.45 }
export const fmt = (n?: number | null, d = 1) => (n == null ? '–' : n.toFixed(d))
export const SCORED = ['Hook & Opening', 'Retention & Pacing', 'Shareability', 'Audio & Music', 'Trend Alignment', 'Visual Quality & Originality', 'On-Screen Text']
