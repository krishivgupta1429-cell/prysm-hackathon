// Built during the Meta Global AI Developer Hackathon (new work).
// Google Fonts picker for the Auto-edit style section: search, category chips, a live preview of each family (the browser loads only the
// letters of the name from Google Fonts). Requests go through authFetch.
import { useEffect, useState } from 'react'
import { apiUrl } from '@/lib/api'
import { authFetch } from '@/lib/auth'
import { btn2, muted, tag } from './ui'

type FontRow = { family: string; category: string; devanagari: boolean }
const CATS = ['all', 'display', 'sans', 'serif', 'rounded', 'handwriting', 'mono', 'devanagari']
const GF_LINK = 'gf-preview-'

function useFontPreview(families: string[]) {
  useEffect(() => {
    families.slice(0, 14).forEach(f => {
      const id = GF_LINK + f.replace(/\W+/g, '-')
      if (document.getElementById(id)) return
      const l = document.createElement('link'); l.id = id; l.rel = 'stylesheet'
      l.href = `https://fonts.googleapis.com/css2?family=${encodeURIComponent(f).replace(/%20/g, '+')}&text=${encodeURIComponent(f + ' Aa Hello')}&display=swap`
      document.head.appendChild(l)
    })
  }, [families])
}

export default function FontPicker({ label, value, onPick, busy }: { label: string; value?: string | null; onPick: (family: string) => void; busy: boolean }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [cat, setCat] = useState('all')
  const [rows, setRows] = useState<FontRow[]>([])
  useEffect(() => {
    if (!open) return
    const h = window.setTimeout(() => {
      const u = `/prysm/auto-edit/fonts?q=${encodeURIComponent(q)}${cat !== 'all' ? `&category=${cat}` : ''}`
      authFetch(apiUrl(u)).then(r => (r.ok ? r.json() : { fonts: [] })).then(j => setRows(j.fonts || [])).catch(() => {})
    }, 200)
    return () => window.clearTimeout(h)
  }, [open, q, cat])
  useFontPreview(open ? rows.map(r => r.family) : value ? [value] : [])
  return (
    <div style={{ flex: '1 1 220px', minWidth: 0 }}>
      <div style={{ ...muted, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.1em', fontSize: 10, marginBottom: 4 }}>{label}</div>
      <button style={{ ...btn2, width: '100%', textAlign: 'left', fontFamily: value ? `'${value}', sans-serif` : undefined, fontSize: 15 }} onClick={() => setOpen(o => !o)} aria-expanded={open} aria-label={`${label} font: ${value || 'default'}`}>{value || 'Poppins'} <span style={{ ...muted, float: 'right' }}>{open ? '▲' : '▼'}</span></button>
      {open && (
        <div style={{ border: '1px solid #e5e5e5', borderRadius: 12, padding: 10, marginTop: 6, background: '#fff' }}>
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search Google Fonts" aria-label="Search fonts" style={{ width: '100%', boxSizing: 'border-box', border: '1px solid #d4d4d4', borderRadius: 999, padding: '8px 14px', fontSize: 13 }} />
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '8px 0' }}>
            {CATS.map(c => <button key={c} onClick={() => setCat(c)} style={{ ...tag, cursor: 'pointer', background: c === cat ? '#0a0a0a' : '#fff', color: c === cat ? '#fff' : '#737373' }}>{c}</button>)}
          </div>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, maxHeight: 260, overflowY: 'auto' }}>
            {rows.map(r => (
              <li key={r.family}><button disabled={busy} onClick={() => { onPick(r.family); setOpen(false) }} style={{ all: 'unset', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', gap: 8, width: '100%', padding: '7px 4px', borderTop: '1px solid #f0f0f0', boxSizing: 'border-box' }}>
                <span style={{ fontFamily: `'${r.family}', sans-serif`, fontSize: 17 }}>{r.family}</span>
                <span style={{ ...muted, fontSize: 10, whiteSpace: 'nowrap' }}>{r.category}{r.devanagari ? ' · हिन्दी' : ''}</span>
              </button></li>
            ))}
            {rows.length === 0 && <li style={muted}>No fonts found.</li>}
          </ul>
        </div>
      )}
    </div>
  )
}
