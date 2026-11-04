// Built during the Meta Global AI Developer Hackathon (new work).
// The Auto-edit card on the results page. No edit yet: "Auto-edit this reel", one line about what it does and a button to /edit/<id>.
// An edit exists: the after-thumbnail, three key numbers (length, loudness, pauses removed), the number of kept changes and a strong
// "Open Auto-edit studio" button. Renders nothing when the backend has Auto-edit off, when the analysis did not come from an upload or
// when the user is signed out. The card wraps on a phone and is never clipped (no overflow hidden on the card).
import { Link } from 'react-router-dom'
import { getToken } from '@/lib/auth'
import { useCountUp } from '@/lib/motion'
import { keptChanges, pausesRemoved, useAutoEdit, useBlobUrl } from './autoedit/api'
import { btn, INK2, tag } from './autoedit/ui'

function Num({ label, value, unit, d = 0 }: { label: string; value: number | null | undefined; unit: string; d?: number }) {
  const v = useCountUp(value ?? null)
  return (
    <div style={{ minWidth: 0 }} data-testid={`ae-${label.toLowerCase()}`}>
      <div style={{ fontSize: 12, color: INK2 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 800, lineHeight: 1.2 }}>{v == null ? '–' : v.toFixed(d)}<span style={{ fontSize: 12, fontWeight: 500, color: INK2 }}> {unit}</span></div>
    </div>
  )
}

export default function AutoEditCard({ analysisId }: { analysisId?: string }) {
  const { enabled, data, base } = useAutoEdit(analysisId, 5000)
  const st = data?.state || null
  const finished = !!st && !st.running && !!st.final
  const { url: thumb } = useBlobUrl(finished ? `${base}/download/cover` : null, 0)
  if (!analysisId || !getToken() || enabled !== true || !data) return null
  if (!st && !data.eligible) return null
  const kept = keptChanges(st).length
  const m = st?.final?.metrics
  const status = !st ? '' : st.running ? 'Working on it' : st.status === 'done' ? 'Done' : st.status === 'stopped' ? 'Stopped early' : st.status === 'failed' ? 'Failed' : st.status
  return (
    <div className="p-card p-fade" style={{ padding: 16, marginBottom: 16 }} data-testid="auto-edit-card">
      <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        {st && (
          <div style={{ width: 72, height: 128, borderRadius: 10, background: 'rgba(31,31,31,0.9)', overflow: 'hidden', flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {thumb ? <img src={thumb} alt="The edited video's cover" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : <span style={{ color: '#d4d4d4', fontSize: 12, textAlign: 'center', padding: 4 }}>{status}</span>}
          </div>
        )}
        <div style={{ flex: '1 1 240px', minWidth: 0 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 16, fontWeight: 700 }}>{st ? 'Your Auto-edit' : 'Auto-edit this reel'}</span>
            <span style={tag}>{st ? status : 'New'}</span>
          </div>
          <p className="p-note" style={{ margin: '4px 0 0' }} data-testid="auto-edit-card-line">
            {st
              ? (st.running ? 'The edit is being made.' : `${kept} change${kept === 1 ? '' : 's'} kept after checking.`)
              : 'Prysm makes its own fixes (pacing, captions, hook text, loudness), checks each one and undoes what does not help. Your original is never changed.'}
          </p>
          {finished && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 10, marginTop: 10 }} data-testid="ae-numbers">
              <Num label="Length" value={m?.duration_s} unit="s" d={1} />
              <Num label="Loudness" value={m?.lufs} unit="LUFS" d={1} />
              <Num label="Pauses" value={pausesRemoved(st)} unit="removed" />
            </div>
          )}
        </div>
      </div>
      <Link to={`/edit/${analysisId}`} style={{ ...btn, textDecoration: 'none', display: 'block', textAlign: 'center', marginTop: 14, padding: '12px 18px', fontSize: 14 }}>
        {st ? 'Open Auto-edit studio' : 'Auto-edit this reel'}
      </Link>
    </div>
  )
}
