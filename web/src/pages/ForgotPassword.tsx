import { useState } from 'react'
import { Link } from 'react-router-dom'
import { apiUrl } from '@/lib/api'

export default function ForgotPassword() {
  const [email, setEmail] = useState('')
  const [status, setStatus] = useState<'idle' | 'loading' | 'sent'>('idle')
  const [error, setError] = useState('')

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!email) { setError('Enter your email'); return }

    setError('')
    setStatus('loading')

    try {
      const res = await fetch(apiUrl('/auth/forgot-password'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      })
      if (res.ok) {
        setStatus('sent')
      } else {
        const data = await res.json()
        setError(data.detail || 'Something went wrong')
        setStatus('idle')
      }
    } catch {
      setError('Could not connect to server')
      setStatus('idle')
    }
  }

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col">
      <nav className="border-b border-border px-4 sm:px-8 h-14 flex items-center">
        <a href="/" className="font-logo text-[21px] text-foreground">Prysm</a>
      </nav>

      <div className="flex-1 flex items-center justify-center px-6">
        <div className="w-full max-w-sm" style={{ opacity: 0, animation: 'fade-slide-in 0.6s ease-out forwards' }}>

          {status === 'sent' ? (
            <div className="text-center">
              <div className="w-14 h-14 rounded-full bg-foreground/5 border border-border flex items-center justify-center mx-auto mb-6">
                <svg className="w-6 h-6 text-foreground/60" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75" />
                </svg>
              </div>
              <h1 className="font-display text-2xl font-bold tracking-tight mb-2">Check your email</h1>
              <p className="text-foreground/40 text-sm font-body mb-8">
                If an account exists with <span className="text-foreground/60">{email}</span>, we've sent a password reset link.
              </p>
              <Link to="/login"
                className="inline-block w-full font-display font-semibold text-[15px] py-3.5 rounded-xl bg-foreground text-background hover:opacity-90 transition-all">
                Back to login
              </Link>
            </div>
          ) : (
            <>
              <h1 className="font-display text-3xl font-bold tracking-tight mb-2">Forgot password?</h1>
              <p className="text-foreground/40 text-sm mb-8 font-body">
                Enter your email and we'll send you a reset link.
              </p>

              <form onSubmit={handleSubmit} className="flex flex-col gap-4">
                <div>
                  <label className="text-[10px] font-semibold tracking-widest uppercase text-foreground/25 mb-1.5 block">Email</label>
                  <input
                    type="email" value={email} onChange={e => { setEmail(e.target.value); setError('') }}
                    placeholder="you@example.com"
                    autoFocus
                    className="w-full border-2 border-border rounded-xl px-4 py-3.5 text-sm text-foreground placeholder:text-foreground/20 outline-none focus:border-foreground/25 transition-all font-body bg-background"
                  />
                </div>

                {error && (
                  <div className="px-4 py-2.5 rounded-xl bg-red-50/50 border border-red-200/50">
                    <p className="text-red-600 text-sm font-body">{error}</p>
                  </div>
                )}

                <button
                  type="submit" disabled={status === 'loading'}
                  className="w-full font-display font-semibold text-[15px] py-3.5 rounded-xl bg-foreground text-background cursor-pointer hover:opacity-90 hover:shadow-lg hover:shadow-foreground/10 hover:-translate-y-0.5 transition-all disabled:opacity-50 disabled:cursor-not-allowed mt-1"
                >
                  {status === 'loading' ? 'Sending...' : 'Send reset link'}
                </button>
              </form>

              <p className="text-center text-sm text-foreground/25 mt-8 font-body">
                Remember your password?{' '}
                <Link to="/login" className="text-foreground/60 hover:text-foreground transition-colors font-medium">Log in</Link>
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
