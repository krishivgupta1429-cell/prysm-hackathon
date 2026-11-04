// Built during the Meta Global AI Developer Hackathon (new work).
// The compact Auto-edit card on the results page. No edit yet: "Auto-edit this reel", one line about what it does and a button to
// /edit/<id>. An edit exists: a small after-thumbnail (or its status), the number of kept changes and "Open Auto-edit studio".
// Renders nothing when the backend has Auto-edit off, when the analysis did not come from an upload or when the user is signed out.
import { Link } from 'react-router-dom'
import { getToken } from '@/lib/auth'
import { keptChanges, useAutoEdit, useBlobUrl } from './autoedit/api'
import { btn, tag } from './autoedit/ui'

export default function AutoEditCard({ analysisId }: { analysisId?: string }) {
  const { enabled, data, base } = useAutoEdit(analysisId, 5000)
  const st = data?.state || null
  const { url: thumb } = useBlobUrl(st && !st.running && st.final ? `${base}/download/cover` : null, 0)
  if (!analysisId || !getToken() || enabled !== true || !data) return null
  if (!st && !data.eligible) return null
  const kept = keptChanges(st).length
  const status = !st ? '' : st.running ? 'Working on it' : st.status === 'done' ? 'Done' : st.status === 'stopped' ? 'Stopped early' : st.status === 'failed' ? 'Failed' : st.status
  return (
    <div style={{ border: '1px solid hsl(var(--border))', borderRadius: 16, padding: 16, background: 'hsl(var(--background))', color: 'hsl(var(--foreground))', marginBottom: 16, display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }} data-testid="auto-edit-card">
      {st && (
        <div style={{ width: 54, height: 96, borderRadius: 8, background: '#0a0a0a', overflow: 'hidden', flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          {thumb ? <img src={thumb} alt="The edited video's cover" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : <span style={{ color: '#a3a3a3', fontSize: 10, textAlign: 'center', padding: 4 }}>{status}</span>}
        </div>
      )}
      <div style={{ flex: '1 1 220px', minWidth: 0 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 15, fontWeight: 700 }}>{st ? 'Your Auto-edit' : 'Auto-edit this reel'}</span>
          <span style={tag}>{st ? status : 'New'}</span>
        </div>
        <p style={{ fontSize: 12, color: '#737373', lineHeight: 1.45, margin: '4px 0 0' }} data-testid="auto-edit-card-line">
          {st
            ? (st.running ? 'The edit is being made.' : `${kept} change${kept === 1 ? '' : 's'} kept after checking.`)
            : 'Prysm makes its own fixes (pacing, captions, hook text, loudness), checks each one and undoes what does not help. Your original is never changed.'}
        </p>
      </div>
      <Link to={`/edit/${analysisId}`} style={{ ...btn, textDecoration: 'none', display: 'inline-block' }}>{st ? 'Open Auto-edit studio' : 'Auto-edit this reel'}</Link>
    </div>
  )
}
