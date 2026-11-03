import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { setToken, setRefreshToken, fetchUser } from '@/lib/auth'
import { apiUrl } from '@/lib/api'

const VALUE_PROPS = [
  { title: '10-parameter analysis', desc: 'Based on real Instagram ranking signals' },
  { title: 'AI-powered suggestions', desc: 'Step-by-step fixes you can apply in minutes' },
  { title: 'Trending hashtags & captions', desc: 'Real data from your niche, ready to copy' },
  { title: 'Competitor breakdowns', desc: 'Learn what makes their content work' },
]

export default function Register() {
  const [email, setEmail] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)

  const pwChecks = [
    { label: '8+ characters', met: password.length >= 8 },
    { label: 'Uppercase', met: /[A-Z]/.test(password) },
    { label: 'Lowercase', met: /[a-z]/.test(password) },
    { label: 'Number', met: /[0-9]/.test(password) },
  ]
  const navigate = useNavigate()

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!email || !username || !password) { setError('Fill in all fields'); return }
    if (username.length < 3) { setError('Username must be at least 3 characters'); return }
    if (password.length < 8) { setError('Password must be at least 8 characters'); return }
    if (!/[A-Z]/.test(password)) { setError('Password must contain an uppercase letter'); return }
    if (!/[a-z]/.test(password)) { setError('Password must contain a lowercase letter'); return }
    if (!/[0-9]/.test(password)) { setError('Password must contain a number'); return }

    setError('')
    setLoading(true)

    try {
      const res = await fetch(apiUrl('/auth/register'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, username, password }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Registration failed')

      setToken(data.access_token)
      if (data.refresh_token) setRefreshToken(data.refresh_token)

      if (data.is_verified) {
        await fetchUser()
        navigate('/dashboard')
      } else {
        navigate(`/verify-email?email=${encodeURIComponent(data.email || email)}`)
      }
    } catch (err: any) {
      setError(err.message || 'Registration failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col">
      <nav className="border-b border-border px-4 sm:px-8 h-14 flex items-center">
        <a href="/" className="font-logo text-[21px] text-foreground">Prysm</a>
      </nav>

      <div className="flex-1 flex">
        {/* ─── LEFT: Value prop panel ─── */}
        <div className="hidden lg:flex flex-1 flex-col justify-center px-16 relative overflow-hidden"
          style={{ background: 'linear-gradient(135deg, #1a1714 0%, #292420 50%, #1a1714 100%)' }}>

          {/* Subtle geometric decoration */}
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[500px] opacity-[0.03]">
            <svg viewBox="0 0 500 500" fill="none">
              <polygon points="250,50 450,400 50,400" stroke="white" strokeWidth="1" />
              <polygon points="250,120 380,360 120,360" stroke="white" strokeWidth="0.5" />
            </svg>
          </div>

          <div className="relative z-10 max-w-md">
            <h2 className="font-display text-3xl font-bold text-white/90 tracking-tight mb-3">
              Stop guessing.<br />Start knowing.
            </h2>
            <p className="text-white/35 font-body text-base mb-10">
              Every viral video follows patterns. Prysm shows you exactly what those patterns are — for your content.
            </p>

            <div className="flex flex-col gap-5">
              {VALUE_PROPS.map((prop, i) => (
                <div key={i} className="flex items-start gap-3"
                  style={{ opacity: 0, animation: `fade-slide-in 0.5s ease-out ${300 + i * 100}ms forwards` }}>
                  <div className="w-5 h-5 rounded-full border border-white/15 flex items-center justify-center shrink-0 mt-0.5">
                    <svg className="w-2.5 h-2.5 text-white/40" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                    </svg>
                  </div>
                  <div>
                    <p className="text-sm text-white/70 font-medium">{prop.title}</p>
                    <p className="text-[12px] text-white/25 font-body">{prop.desc}</p>
                  </div>
                </div>
              ))}
            </div>

          </div>
        </div>

        {/* ─── RIGHT: Register form ─── */}
        <div className="flex-1 flex items-center justify-center px-6">
          <div className="w-full max-w-sm" style={{ opacity: 0, animation: 'fade-slide-in 0.6s ease-out forwards' }}>
            <h1 className="font-display text-3xl font-bold tracking-tight mb-2">Create your account</h1>
            <p className="text-foreground/40 text-sm mb-8 font-body">Start analysing your content — it's free</p>

            {/* Google Sign-Up */}
            <button
              type="button"
              onClick={async () => {
                try {
                  const res = await fetch(apiUrl('/auth/google/url'))
                  const data = await res.json()
                  if (!data.url) return
                  // Mobile browsers block window.open — use full-page redirect instead
                  const isMobile = /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent)
                  if (isMobile) {
                    window.location.href = data.url
                    return
                  }
                  const popup = window.open(data.url, 'google_auth', 'width=500,height=600,scrollbars=yes')
                  const handler = (e: MessageEvent) => {
                    if (e.origin !== window.location.origin) return
                    if (e.data?.type === 'google_auth_success') {
                      window.removeEventListener('message', handler)
                      popup?.close()
                      window.location.href = e.data.destination
                    }
                  }
                  window.addEventListener('message', handler)
                } catch {}
              }}
              className="w-full flex items-center justify-center gap-3 px-4 py-3.5 rounded-xl border-2 border-border bg-transparent text-foreground/60 text-sm font-medium cursor-pointer hover:border-foreground/20 hover:bg-secondary/30 transition-all group"
            >
              <svg className="w-5 h-5 group-hover:scale-110 transition-transform" viewBox="0 0 24 24">
                <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/>
                <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
              </svg>
              Continue with Google
            </button>

            <div className="flex items-center gap-4 my-6">
              <div className="flex-1 h-px bg-border" />
              <span className="text-[11px] text-foreground/20 font-body">or sign up with email</span>
              <div className="flex-1 h-px bg-border" />
            </div>

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

              <div>
                <label className="text-[10px] font-semibold tracking-widest uppercase text-foreground/25 mb-1.5 block">Creator Handle</label>
                <div className="relative">
                  <span className="absolute left-4 top-1/2 -translate-y-1/2 text-sm text-foreground/30 font-body">@</span>
                  <input
                    type="text" value={username} onChange={e => { setUsername(e.target.value.replace(/[^a-zA-Z0-9._]/g, '').toLowerCase()); setError('') }}
                    placeholder="your_instagram_handle"
                    className="w-full border-2 border-border rounded-xl pl-8 pr-4 py-3.5 text-sm text-foreground placeholder:text-foreground/20 outline-none focus:border-foreground/25 transition-all font-body bg-background"
                  />
                </div>
                <p className="text-[10px] text-foreground/20 font-body mt-1.5">Your unique creator handle — use your Instagram username</p>
              </div>

              <div>
                <label className="text-[10px] font-semibold tracking-widest uppercase text-foreground/25 mb-1.5 block">Password</label>
                <div className="relative">
                  <input
                    type={showPassword ? 'text' : 'password'} value={password} onChange={e => { setPassword(e.target.value); setError('') }}
                    placeholder="Min 8 chars, uppercase, lowercase, number"
                    className="w-full border-2 border-border rounded-xl px-4 py-3.5 pr-12 text-sm text-foreground placeholder:text-foreground/20 outline-none focus:border-foreground/25 transition-all font-body bg-background"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 p-1 text-foreground/25 hover:text-foreground/50 transition-colors"
                    tabIndex={-1}
                  >
                    {showPassword ? (
                      <svg className="w-4.5 h-4.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
                      </svg>
                    ) : (
                      <svg className="w-4.5 h-4.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
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

              {error && (
                <div className="px-4 py-2.5 rounded-xl bg-red-50/50 border border-red-200/50">
                  <p className="text-red-600 text-sm font-body">{error}</p>
                </div>
              )}

              <button
                type="submit" disabled={loading}
                className="w-full font-display font-semibold text-[15px] py-3.5 rounded-xl bg-foreground text-background cursor-pointer hover:opacity-90 hover:shadow-lg hover:shadow-foreground/10 hover:-translate-y-0.5 transition-all disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:translate-y-0 disabled:hover:shadow-none mt-1"
              >
                {loading ? 'Creating account...' : 'Create account →'}
              </button>

              <p className="text-center text-[11px] text-foreground/35 font-body leading-relaxed">
                By proceeding, you agree to our{' '}
                <a href="/terms" target="_blank" className="text-foreground/60 hover:text-foreground underline underline-offset-2 transition-colors">Terms</a>
                {' '}and{' '}
                <a href="/privacy" target="_blank" className="text-foreground/60 hover:text-foreground underline underline-offset-2 transition-colors">Privacy Policy</a>
              </p>
            </form>

            <p className="text-center text-sm text-foreground/25 mt-8 font-body">
              Already have an account?{' '}
              <Link to="/login" className="text-foreground/60 hover:text-foreground transition-colors font-medium">Log in</Link>
            </p>

          </div>
        </div>
      </div>
    </div>
  )
}
