// Built during the Meta Global AI Developer Hackathon (new work).
// The Creator DNA page before Instagram is connected: a short, benefit-focused pitch and one button. The privacy and deletion details live on
// the Privacy page (/privacy), not here.
import { Button } from '@/components/ui/button'
import type { Status } from './types'

const BENEFITS: { icon: string; text: string }[] = [
  { icon: 'M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z',
    text: 'Your best and weakest reels, compared' },
  { icon: 'M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z',
    text: 'Hook and pacing patterns that move your numbers' },
  { icon: 'M9 12.75L11.25 15 15 9.75M21 12c0 4.97-4.03 9-9 9s-9-4.03-9-9 4.03-9 9-9 9 4.03 9 9z',
    text: 'Every new video checked against your own history' },
]

export function Consent({ status, onConnect, busy, error }: { status: Status; onConnect: () => void; busy: boolean; error: string }) {
  const available = status.configured || status.fixtureAvailable
  return (
    <div className="rounded-2xl border border-border bg-background p-6 sm:p-10 text-center" data-testid="consent">
      <h2 className="font-display text-2xl sm:text-4xl font-black tracking-tight">Learn what works in your own reels</h2>
      <p className="mt-3 text-[14px] sm:text-base text-foreground/55 font-body leading-relaxed max-w-xl mx-auto">
        See what actually works in your own reels, based on the numbers Instagram measured.
      </p>
      <ul className="mt-7 grid gap-3 sm:grid-cols-3 text-left" data-testid="benefits">
        {BENEFITS.map(b => (
          <li key={b.text} className="rounded-xl border border-border p-4 flex sm:flex-col items-center sm:items-start gap-3">
            <span className="w-9 h-9 rounded-lg bg-foreground/[0.04] flex items-center justify-center shrink-0">
              <svg className="w-[18px] h-[18px] text-foreground/60" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.6} aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d={b.icon} />
              </svg>
            </span>
            <span className="text-[13px] sm:text-[14px] leading-snug text-foreground/75 font-body">{b.text}</span>
          </li>
        ))}
      </ul>
      {!available && <p className="mt-6 text-[14px] text-foreground/55 font-body" data-testid="not-available">Connecting Instagram is not available right now.</p>}
      {error && <p className="mt-6 text-[14px] font-bold text-destructive font-body" role="alert">{error}</p>}
      <Button className="mt-7 h-12 px-8 text-[15px] font-display font-semibold bg-foreground text-background hover:bg-foreground/90" disabled={!available || busy} onClick={onConnect} data-testid="connect">
        Connect Instagram
      </Button>
      <p className="mt-3 text-[12px] text-foreground/40 font-body">Read-only access. Disconnect anytime.</p>
    </div>
  )
}
