import { useState, useEffect, useRef } from 'react'
import { useSearchParams, Link, useNavigate } from 'react-router-dom'
import { apiUrl } from '@/lib/api'
import { getToken, fetchUser } from '@/lib/auth'

export default function VerifyEmail() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')
  const emailParam = searchParams.get('email')
  const [status, setStatus] = useState<'loading' | 'success' | 'error' | 'waiting'>(token ? 'loading' : 'waiting')
  const [message, setMessage] = useState('')
  const [resendEmail, setResendEmail] = useState(emailParam || '')
  const [resendStatus, setResendStatus] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle')
  const navigate = useNavigate()
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Verify token if present (user clicked email link)
  useEffect(() => {
    if (!token) return
    fetch(apiUrl('/auth/verify-email'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    })
      .then(async (res) => {
        const data = await res.json()
        if (res.ok) {
          setStatus('success')
          setMessage(data.message)
          // Redirect to dashboard — this is the tab the user is looking at
          setTimeout(() => navigate('/dashboard'), 1500)
        } else {
          setStatus('error')
          setMessage(data.detail || 'Verification failed')
        }
      })
      .catch(() => {
        setStatus('error')
        setMessage('Could not connect to server')
      })
  }, [token])

  // Poll /auth/me every 4 seconds to check if user got verified (in another tab)
  useEffect(() => {
    if (status !== 'waiting') return
    const authToken = getToken()
    if (!authToken) return

    pollRef.current = setInterval(async () => {
      try {
        const user = await fetchUser()
        if (user?.is_verified) {
          if (pollRef.current) clearInterval(pollRef.current)
          navigate('/dashboard')
        }
      } catch {}
    }, 4000)

    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [status])

  async function handleResend() {
    if (!resendEmail) return
    setResendStatus('sending')
    try {
      const res = await fetch(apiUrl('/auth/resend-verification'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: resendEmail }),
      })
      if (res.ok) setResendStatus('sent')
      else setResendStatus('error')
    } catch {
      setResendStatus('error')
    }
  }

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col">
      <nav className="border-b border-border px-4 sm:px-8 h-14 flex items-center">
        <a href="/" className="font-logo text-[21px] text-foreground">Prysm</a>
      </nav>

      <div className="flex-1 flex items-center justify-center px-6">
        <div className="w-full max-w-md text-center" style={{ opacity: 0, animation: 'fade-slide-in 0.6s ease-out forwards' }}>

          {status === 'loading' && (
            <>
              <div className="w-12 h-12 rounded-full border-2 border-foreground/10 border-t-foreground/50 animate-spin mx-auto mb-6" />
              <h1 className="font-display text-2xl font-bold tracking-tight mb-2">Verifying your email...</h1>
              <p className="text-foreground/40 text-sm font-body">Please wait a moment.</p>
            </>
          )}

          {status === 'success' && (
            <>
              <div className="w-16 h-16 rounded-full bg-foreground/5 border border-border flex items-center justify-center mx-auto mb-6">
                <svg className="w-7 h-7 text-foreground/60" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                </svg>
              </div>
              <h1 className="font-display text-3xl font-bold tracking-tight mb-3">You're verified</h1>
              <p className="text-foreground/40 text-sm font-body mb-8">Your email has been confirmed. Redirecting to your dashboard...</p>
              <Link to="/dashboard"
                className="inline-block w-full max-w-xs font-display font-semibold text-[15px] py-3.5 rounded-xl bg-foreground text-background hover:opacity-90 transition-all">
                Go to Dashboard →
              </Link>
            </>
          )}

          {status === 'error' && (
            <>
              <div className="w-16 h-16 rounded-full bg-foreground/5 border border-border flex items-center justify-center mx-auto mb-6">
                <svg className="w-7 h-7 text-foreground/40" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
                </svg>
              </div>
              <h1 className="font-display text-2xl font-bold tracking-tight mb-2">Link expired or already used</h1>
              <p className="text-foreground/40 text-sm font-body mb-8">
                If you've already verified your email, you can go straight to your dashboard.
              </p>

              <Link to="/dashboard"
                className="inline-block w-full max-w-xs font-display font-semibold text-[15px] py-3.5 rounded-xl bg-foreground text-background hover:opacity-90 transition-all mb-4">
                Go to Dashboard →
              </Link>

              <div className="mt-6 pt-6 border-t border-border/50 max-w-xs mx-auto">
                <p className="text-foreground/30 text-[12px] font-body mb-4">Need a new verification link?</p>
                <div className="flex flex-col gap-3">
                  <input
                    type="email" value={resendEmail} onChange={e => setResendEmail(e.target.value)}
                    placeholder="Enter your email"
                    className="w-full border-2 border-border rounded-xl px-4 py-3 text-sm text-foreground placeholder:text-foreground/20 outline-none focus:border-foreground/25 transition-all font-body bg-background"
                  />
                  <button
                    onClick={handleResend}
                    disabled={resendStatus === 'sending' || resendStatus === 'sent' || !resendEmail}
                    className="w-full font-display font-semibold text-[13px] py-2.5 rounded-xl border-2 border-border text-foreground/50 hover:border-foreground/20 transition-all disabled:opacity-50"
                  >
                    {resendStatus === 'sending' ? 'Sending...' : resendStatus === 'sent' ? 'Email sent — check your inbox' : 'Resend verification email'}
                  </button>
                </div>
              </div>
            </>
          )}

          {status === 'waiting' && (
            <>
              <div className="w-16 h-16 rounded-full bg-foreground/5 border border-border flex items-center justify-center mx-auto mb-6">
                <svg className="w-7 h-7 text-foreground/40" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75" />
                </svg>
              </div>
              <h1 className="font-display text-3xl font-bold tracking-tight mb-3">Check your inbox</h1>
              <p className="text-foreground/40 text-[15px] font-body mb-2 leading-relaxed">
                We sent a verification link to
              </p>
              {emailParam && (
                <p className="text-foreground/70 text-[15px] font-body font-medium mb-6">{emailParam}</p>
              )}
              <p className="text-foreground/30 text-sm font-body mb-3 max-w-xs mx-auto leading-relaxed">
                Click the link in the email to verify your account. This page will automatically redirect once you're verified.
              </p>
              <div className="flex items-center justify-center gap-2 mb-10">
                <div className="w-1.5 h-1.5 rounded-full bg-foreground/20 animate-pulse" />
                <p className="text-foreground/20 text-[12px] font-body">Waiting for verification...</p>
              </div>

              <div className="flex flex-col gap-3 max-w-xs mx-auto">
                {!emailParam && (
                  <input
                    type="email" value={resendEmail} onChange={e => setResendEmail(e.target.value)}
                    placeholder="Enter your email"
                    className="w-full border-2 border-border rounded-xl px-4 py-3 text-sm text-foreground placeholder:text-foreground/20 outline-none focus:border-foreground/25 transition-all font-body bg-background"
                  />
                )}
                <button
                  onClick={handleResend}
                  disabled={resendStatus === 'sending' || resendStatus === 'sent' || !resendEmail}
                  className="w-full font-display font-semibold text-[14px] py-3 rounded-xl border-2 border-border text-foreground/60 hover:border-foreground/20 transition-all disabled:opacity-50"
                >
                  {resendStatus === 'sending' ? 'Sending...' : resendStatus === 'sent' ? 'Email sent — check your inbox' : "Didn't get it? Resend email"}
                </button>
              </div>

              <div className="mt-10 pt-6 border-t border-border/50 max-w-xs mx-auto">
                <p className="text-foreground/20 text-[12px] font-body mb-3">
                  Check your spam folder if you don't see the email
                </p>
                <Link to="/login" className="text-foreground/40 hover:text-foreground/60 transition-colors font-medium text-sm font-body">
                  ← Back to login
                </Link>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
