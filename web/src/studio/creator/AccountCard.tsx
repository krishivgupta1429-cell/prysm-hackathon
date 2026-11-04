// Built during the Meta Global AI Developer Hackathon (new work).
// The connected-account card: picture (or initial), @handle, connected status, last import time, and a small menu with
// Re-import and Disconnect (Disconnect asks for confirmation in a dialog).
import { useState } from 'react'
import { MoreHorizontal } from 'lucide-react'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { timeAgo } from './format'

export function AccountCard({ username, pictureUrl, fixture, lastImportAt, imported, busy, onReimport, onDisconnect }: {
  username: string | null; pictureUrl?: string | null; fixture: boolean; lastImportAt?: string | null; imported?: number
  busy: boolean; onReimport: () => void; onDisconnect: () => void
}) {
  const [confirm, setConfirm] = useState(false)
  const handle = fixture ? 'fixture_creator' : (username || 'your account')
  return (
    <div className="rounded-2xl border border-border bg-background p-4 sm:p-5 flex items-center gap-4" data-testid="account-card">
      <Avatar className="h-14 w-14 border border-border">
        {pictureUrl && !fixture && <AvatarImage src={pictureUrl} alt={`Profile picture of @${handle}`} referrerPolicy="no-referrer" />}
        <AvatarFallback className="bg-primary text-foreground font-display font-black text-lg">{handle.slice(0, 1).toUpperCase()}</AvatarFallback>
      </Avatar>
      <div className="min-w-0 flex-1">
        <p className="font-display text-lg font-black tracking-tight truncate" data-testid="handle">@{handle}</p>
        <p className="flex items-center gap-1.5 text-[12px] text-foreground/55 font-body flex-wrap">
          <span className="inline-flex items-center gap-1 font-bold text-green-700" data-testid="connected-status"><span className="h-1.5 w-1.5 rounded-full bg-green-600" aria-hidden="true" />Connected</span>
          <span aria-hidden="true">·</span>
          <span data-testid="last-imported">{lastImportAt ? `Last imported ${timeAgo(lastImportAt)}${imported != null ? ` (${imported} reel${imported === 1 ? '' : 's'})` : ''}` : 'Nothing imported yet'}</span>
          {fixture && <span className="text-[10px] font-bold tracking-wide uppercase border border-border rounded-full px-2 py-0.5">Test data</span>}
        </p>
      </div>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="icon" aria-label="Account actions" data-testid="account-menu"><MoreHorizontal className="h-4 w-4" /></Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="font-body">
          <DropdownMenuItem onSelect={onReimport} disabled={busy} data-testid="menu-reimport">Re-import reels</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setConfirm(true)} className="text-destructive focus:text-destructive" data-testid="menu-disconnect">Disconnect Instagram</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent data-testid="disconnect-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Disconnect Instagram?</AlertDialogTitle>
            <AlertDialogDescription className="font-body">
              This deletes your Instagram token and every reel, insight, hook-word transcript, link and Creator DNA stored for you. Your Prysm analyses stay (only their
              &ldquo;compared to your reels&rdquo; block is removed). You can connect again any time.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it connected</AlertDialogCancel>
            <AlertDialogAction className="bg-destructive text-destructive-foreground hover:bg-destructive/90" onClick={() => { setConfirm(false); onDisconnect() }} data-testid="disconnect-confirm">
              Disconnect and delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
