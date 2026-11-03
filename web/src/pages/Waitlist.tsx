import { getUser } from '@/lib/auth'

export default function Waitlist() {
  const user = getUser()

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col">
      <nav className="border-b border-border px-4 sm:px-8 h-14 flex items-center justify-between">
        <a href="/" className="font-logo text-[21px] text-foreground">Prysm</a>
        <a href="/dashboard" className="text-sm text-foreground/40 hover:text-foreground/60 transition-colors">Dashboard</a>
      </nav>

      <div className="flex-1 flex items-center justify-center px-6">
        <div className="max-w-md text-center" style={{ opacity: 0, animation: 'fade-slide-in 0.6s ease-out forwards' }}>
          <div className="w-16 h-16 rounded-2xl mx-auto mb-6 flex items-center justify-center"
            style={{ background: 'rgba(139,92,246,0.08)' }}>
            <svg className="w-8 h-8" style={{ color: 'rgba(139,92,246,0.6)' }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456z" />
            </svg>
          </div>

          <h1 className="font-display text-2xl sm:text-3xl font-bold tracking-tight mb-3">
            You're on the list
          </h1>
          <p className="text-foreground/40 font-body text-base mb-2">
            Prysm is currently in private beta.
          </p>
          <p className="text-foreground/30 font-body text-sm mb-8">
            We're fine-tuning the AI analysis engine to make sure every score and suggestion is accurate.
            {user ? ` We'll notify you at ${user.email} when it's ready.` : ' Sign up to get notified when we launch.'}
          </p>

          <div className="flex flex-col gap-3">
            <a href="/dashboard"
              className="inline-block px-8 py-3.5 rounded-xl bg-foreground text-background text-sm font-display font-semibold hover:opacity-90 transition-opacity">
              Back to Dashboard
            </a>
            {!user && (
              <a href="/register"
                className="inline-block px-8 py-3 rounded-xl border-2 border-border text-sm font-display font-semibold text-foreground/60 hover:border-foreground/20 transition-all">
                Sign up for early access
              </a>
            )}
          </div>

          <p className="text-[11px] text-foreground/15 font-body mt-10">
            Launching soon — thanks for your patience
          </p>
        </div>
      </div>
    </div>
  )
}
