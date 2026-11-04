// Built during the Meta Global AI Developer Hackathon (new work).
// The consent screen shown before the redirect to Instagram.
import { Button } from '@/components/ui/button'
import type { Status } from './types'

export function Consent({ status, onConnect, busy, error }: { status: Status; onConnect: () => void; busy: boolean; error: string }) {
  const available = status.configured || status.fixtureAvailable
  const Item = ({ k, children }: { k: string; children: React.ReactNode }) => <li className="text-[14px] leading-relaxed text-foreground/75 font-body"><b className="text-foreground">{k}</b> {children}</li>
  return (
    <div className="rounded-2xl border border-border bg-background p-5 sm:p-6" data-testid="consent">
      <h2 className="font-display text-2xl font-black tracking-tight">Connect your Instagram to get your Creator DNA</h2>
      <p className="mt-2 text-[14px] text-foreground/60 font-body">Prysm reads your own recent reels and what Instagram measured for them, finds what your best and weakest reels have in common, and then compares every new video you analyse with your own patterns.</p>
      <ul className="mt-4 space-y-2.5 list-disc pl-5">
        <Item k="What we read:">your own reels (link, date, caption, likes, comments) and their insights (reach, views, watch time, skip rate and similar). Only your account, nothing about other people.</Item>
        <Item k="What we store:">those numbers, a short fingerprint of each reel (hook, format, pacing), the first few spoken words of each reel, and your DNA summary. <b>We never store your videos.</b> A video is downloaded for a moment to cut about six frames and the first seconds of audio, and is deleted straight after.</Item>
        <Item k="Who processes your frames:">the AI model provider (Meta, Muse Spark) receives those frames, the caption and the first seconds of speech to write the fingerprint.</Item>
        <Item k="Deleting it:">Disconnect removes your Instagram token and every reel, insight and DNA we derived, at once. You can also remove Prysm in Instagram under Settings, Apps and websites.</Item>
        <Item k="Permissions asked:">{status.scopes.join(', ')}. Read only; Prysm cannot post or message for you.</Item>
      </ul>
      <p className="mt-3 text-[12px] text-foreground/45 font-body">Needs a Business or Creator Instagram account. Signing in to Prysm stays with email or Google; this is an extra step.</p>
      {!available && <p className="mt-3 text-[14px] font-bold font-body">Instagram is not configured on this server yet, so connecting is unavailable.</p>}
      {error && <p className="mt-3 text-[14px] font-bold text-destructive font-body" role="alert">{error}</p>}
      <Button className="mt-5" disabled={!available || busy} onClick={onConnect} data-testid="connect">
        {status.fixtureAvailable ? 'Connect (test data, no Instagram)' : 'I understand, continue to Instagram'}
      </Button>
    </div>
  )
}
