// Built during the Meta Global AI Developer Hackathon (new work).
// The Instagram + Creator DNA entry on the Dashboard (demo mode), in the style of the action cards above it. Not connected: "Connect Instagram" and a
// Connect button to /creator-dna. Connected: "Connected as @handle", how many reels are imported, "View Creator DNA" and a quiet Disconnect.
// It reads /instagram/status (the same call the Creator DNA page makes); a failed call shows the not-connected card, never an error, and a server
// without Instagram configured hides the card.
import { useCallback, useEffect, useState } from 'react'
import { apiUrl } from '@/lib/api'
import { authFetch } from '@/lib/auth'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Skeleton } from '@/components/ui/skeleton'
import type { Status } from './creator/types'

const ACCENT = '#ec4899'
const ICON = 'M6.827 6.175A2.31 2.31 0 015.186 7.23c-.38.054-.757.112-1.134.175C2.999 7.58 2.25 8.507 2.25 9.574V18a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9.574c0-1.067-.75-1.994-1.802-2.169a47.865 47.865 0 00-1.134-.175 2.31 2.31 0 01-1.64-1.055l-.822-1.316a2.192 2.192 0 00-1.736-1.039 48.774 48.774 0 00-5.232 0 2.192 2.192 0 00-1.736 1.039l-.821 1.316zM16.5 12.75a4.5 4.5 0 11-9 0 4.5 4.5 0 019 0z'

type View = 'loading' | 'hidden' | 'off' | 'on'

export default function InstagramCard() {
  const [view, setView] = useState<View>('loading')
  const [status, setStatus] = useState<Status | null>(null)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const r = await authFetch(apiUrl('/instagram/status'))
      if (!r.ok) throw new Error(String(r.status))
      const s: Status = await r.json()
      setStatus(s)
      setView(s.connected ? 'on' : (!s.configured && !s.fixtureAvailable) ? 'hidden' : 'off')
    } catch {
      setView(v => (v === 'loading' ? 'off' : v)) // a failed call shows the not-connected card, never an error
    }
  }, [])

  useEffect(() => {
    load()
    const again = () => { if (document.visibilityState !== 'hidden') load() }   // coming back from /creator-dna or another tab: show the new state
    window.addEventListener('focus', again)
    window.addEventListener('pageshow', again)
    document.addEventListener('visibilitychange', again)
    return () => { window.removeEventListener('focus', again); window.removeEventListener('pageshow', again); document.removeEventListener('visibilitychange', again) }
  }, [load])

  async function disconnect() {
    setBusy(true)
    try { await authFetch(apiUrl('/instagram/disconnect'), { method: 'DELETE' }) } catch { /* the status below shows what is true */ }
    setBusy(false)
    await load()
  }

  if (view === 'hidden') return null
  const shell = 'rounded-xl sm:rounded-2xl p-3 sm:p-5 bg-background border border-border mb-6 sm:mb-8'
  if (view === 'loading') {
    return (
      <div className={shell} data-testid="ig-card-loading" aria-busy="true" aria-label="Loading Instagram">
        <div className="flex items-center gap-3 sm:gap-4">
          <Skeleton className="w-8 h-8 sm:w-10 sm:h-10 rounded-lg sm:rounded-xl" />
          <div className="flex-1 space-y-2"><Skeleton className="h-3.5 w-40" /><Skeleton className="h-3 w-64 max-w-full" /></div>
        </div>
      </div>
    )
  }

  const connected = view === 'on' && status?.connected
  const acct = status?.account
  const imported = acct?.lastImport?.imported ?? 0
  const handle = acct?.username ? `@${acct.username}` : 'Instagram'
  const btn = 'px-4 py-2 rounded-xl bg-foreground text-background text-[12px] font-body font-bold hover:opacity-85 transition-opacity whitespace-nowrap'
  return (
    <div className={shell} data-testid={connected ? 'ig-card-connected' : 'ig-card-connect'} style={{ opacity: 0, animation: 'fade-slide-in 0.5s ease-out 500ms forwards' }}>
      <div className="flex items-center gap-3 sm:gap-4 flex-wrap sm:flex-nowrap">
        <div className="w-8 h-8 sm:w-10 sm:h-10 rounded-lg sm:rounded-xl flex items-center justify-center shrink-0" style={{ background: `${ACCENT}10`, color: `${ACCENT}90` }}>
          <svg className="w-4 h-4 sm:w-5 sm:h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5} aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d={ICON} /></svg>
        </div>
        <div className="flex-1 min-w-[10rem]">
          {connected ? (
            <>
              <p className="font-display font-semibold text-xs sm:text-sm mb-0.5" data-testid="ig-title">Connected as {handle}{acct?.isFixture ? ' (test data)' : ''}</p>
              <p className="text-[11px] text-foreground/35 font-body" data-testid="ig-line">
                {imported > 0 ? `${imported} reel${imported === 1 ? '' : 's'} imported` : 'No reels imported yet'}
              </p>
            </>
          ) : (
            <>
              <p className="font-display font-semibold text-xs sm:text-sm mb-0.5" data-testid="ig-title">Connect Instagram</p>
              <p className="text-[11px] text-foreground/35 font-body">Personalise every analysis with your real reels</p>
            </>
          )}
        </div>
        <div className="flex items-center gap-4 shrink-0">
          {connected ? (
            <>
              <button type="button" onClick={() => setConfirm(true)} disabled={busy} data-testid="ig-disconnect"
                className="text-[11px] text-foreground/30 hover:text-foreground/55 transition-colors font-body cursor-pointer">Disconnect</button>
              <a href="/creator-dna" className={btn} data-testid="ig-view">View Creator DNA</a>
            </>
          ) : (
            <a href="/creator-dna" className={btn} data-testid="ig-connect">Connect →</a>
          )}
        </div>
      </div>
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent data-testid="ig-disconnect-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Disconnect Instagram?</AlertDialogTitle>
            <AlertDialogDescription className="font-body">
              This deletes your Instagram token and every reel, insight, hook-word transcript, link and Creator DNA stored for you. Your Prysm analyses stay.
              You can connect again any time.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it connected</AlertDialogCancel>
            <AlertDialogAction className="bg-destructive text-destructive-foreground hover:bg-destructive/90" onClick={() => { setConfirm(false); disconnect() }} data-testid="ig-disconnect-confirm">
              Disconnect and delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
