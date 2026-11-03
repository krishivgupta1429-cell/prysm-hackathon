import { useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { apiUrl } from '@/lib/api'

export default function ResetPassword() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [error, setError] = useState('')

  const pwChecks = [
    { label: '8+ characters', met: password.length >= 8 },
    { label: 'Uppercase', met: /[A-Z]/.test(password) },
    { label: 'Lowercase', met: /[a-z]/.test(password) },
    { label: 'Number', met: /[0-9]/.test(password) },
  ]

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!token) { setError('Invalid reset link'); return }
    if (!password) { setError('Enter a new password'); return }
    if (password !== confirm) { setError('Passwords do not match'); return }
    if (!pwChecks.every(c => c.met)) { setError('Password does not meet requirements'); return }

    setError('')
    setStatus('loading')

    try {
      const res = await fetch(apiUrl('/auth/reset-password'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, new_password: password }),
      })
      const data = await res.json()
      if (res.ok) {
        setStatus('success')
      } else {
        setError(data.detail || 'Reset failed')
        setStatus('error')
      }
    } catch {
      setError('Could not connect to server')
      setStatus('error')
    }
  }

  if (!token) {
    return (
      <div className="min-h-screen bg-background text-foreground flex flex-col">
        <nav className="border-b border-border px-4 sm:px-8 h-14 flex items-center">
          <a href="/" className="font-logo text-[21px] text-foreground">Prysm</a>
        </nav>
        <div className="flex-1 flex items-center justify-center px-6">
          <div className="text-center">
            <h1 className="font-display text-2xl font-bold tracking-tight mb-2">Invalid reset link</h1>
            <p className="text-foreground/40 text-sm font-body mb-6">This link is missing a reset token.</p>
            <Link to="/forgot-password" className="text-foreground/60 hover:text-foreground transition-colors font-medium text-sm font-body">
              Request a new reset link
            </Link>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col">
      <nav className="border-b border-border px-4 sm:px-8 h-14 flex items-center">
        <a href="/" className="font-logo text-[21px] text-foreground">Prysm</a>
      </nav>

      <div className="flex-1 flex items-center justify-center px-6">
        <div className="w-full max-w-sm" style={{ opacity: 0, animation: 'fade-slide-in 0.6s ease-out forwards' }}>

          {status === 'success' ? (
            <div className="text-center">
              <div className="w-14 h-14 rounded-full bg-foreground/5 border border-border flex items-center justify-center mx-auto mb-6">
                <svg className="w-6 h-6 text-foreground/60" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                </svg>
              </div>
              <h1 className="font-display text-2xl font-bold tracking-tight mb-2">Password reset</h1>
              <p className="text-foreground/40 text-sm font-body mb-8">Your password has been updated. You can now log in.</p>
              <Link to="/login"
                className="inline-block w-full font-display font-semibold text-[15px] py-3.5 rounded-xl bg-foreground text-background hover:opacity-90 transition-all">
                Log in
              </Link>
            </div>
          ) : (
            <>
              <h1 className="font-display text-3xl font-bold tracking-tight mb-2">Reset password</h1>
              <p className="text-foreground/40 text-sm mb-8 font-body">Choose a new password for your account.</p>

              <form onSubmit={handleSubmit} className="flex flex-col gap-4">
                <div>
                  <label className="text-[10px] font-semibold tracking-widest uppercase text-foreground/25 mb-1.5 block">New password</label>
                  <div className="relative">
                    <input
                      type={showPassword ? 'text' : 'password'} value={password}
                      onChange={e => { setPassword(e.target.value); setError('') }}
                      placeholder="Min 8 chars, uppercase, lowercase, number"
                      className="w-full border-2 border-border rounded-xl px-4 py-3.5 pr-12 text-sm text-foreground placeholder:text-foreground/20 outline-none focus:border-foreground/25 transition-all font-body bg-background"
                    />
                    <button type="button" onClick={() => setShowPassword(!showPassword)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 p-1 text-foreground/25 hover:text-foreground/50 transition-colors"
                      tabIndex={-1}>
                      {showPassword ? (
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
                        </svg>
                      ) : (
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z" />
                          <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                        </svg>
                      )}
                    </button>
                  </div>
                  {password.length > 0 && (
                    <div className="flex gap-3 mt-2.5">
                      {pwChecks.map((c) => (
                        <span key={c.label} className={`text-[10px] font-body flex items-center gap-1 transition-colors ${c.met ? 'text-foreground/50' : 'text-foreground/15'}`}>
                          <svg className={`w-3 h-3 transition-colors ${c.met ? 'text-foreground/50' : 'text-foreground/15'}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                            {c.met
                              ? <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                              : <circle cx="12" cy="12" r="8" strokeWidth={1.5} />
                            }
                          </svg>
                          {c.label}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                <div>
                  <label className="text-[10px] font-semibold tracking-widest uppercase text-foreground/25 mb-1.5 block">Confirm password</label>
                  <input
                    type={showPassword ? 'text' : 'password'} value={confirm}
                    onChange={e => { setConfirm(e.target.value); setError('') }}
                    placeholder="Re-enter your new password"
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
                  {status === 'loading' ? 'Resetting...' : 'Reset password'}
                </button>
              </form>

              <p className="text-center text-sm text-foreground/25 mt-8 font-body">
                <Link to="/login" className="text-foreground/60 hover:text-foreground transition-colors font-medium">Back to login</Link>
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
