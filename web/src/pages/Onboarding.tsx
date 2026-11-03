import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getToken, fetchUser } from '@/lib/auth'
import { apiUrl } from '@/lib/api'

export default function Onboarding() {
  const [handle, setHandle] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const cleaned = handle.replace(/^@/, '').trim()
    if (!cleaned || cleaned.length < 3) {
      setError('Handle must be at least 3 characters')
      return
    }
    if (!/^[a-zA-Z0-9._]+$/.test(cleaned)) {
      setError('Only letters, numbers, dots, and underscores')
      return
    }

    setError('')
    setLoading(true)

    try {
      const token = getToken()
      const res = await fetch(apiUrl('/auth/update-handle'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ username: cleaned.toLowerCase() }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Failed to set handle')

      await fetchUser()
      navigate('/dashboard')
    } catch (err: any) {
      setError(err.message || 'Something went wrong')
    } finally {
      setLoading(false)
    }
  }

  function handleSkip() {
    navigate('/dashboard')
  }

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col">
      <nav className="border-b border-border px-4 sm:px-8 h-14 flex items-center">
        <a href="/" className="font-logo text-[21px] text-foreground">Prysm</a>
      </nav>

      <div className="flex-1 flex items-center justify-center px-6">
        <div className="w-full max-w-sm text-center" style={{ opacity: 0, animation: 'fade-slide-in 0.6s ease-out forwards' }}>
          <div className="w-16 h-16 rounded-2xl bg-foreground/[0.04] border border-border flex items-center justify-center mx-auto mb-6">
            <svg className="w-7 h-7 text-foreground/30" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.676 0-5.216-.584-7.499-1.632z" />
            </svg>
          </div>

          <h1 className="font-display text-2xl font-bold tracking-tight mb-2">What's your creator handle?</h1>
          <p className="text-foreground/40 text-sm font-body mb-8">
            This is how you'll appear on Prysm. Use your Instagram handle so your analysis feels personal.
          </p>

          <form onSubmit={handleSubmit} className="flex flex-col gap-4 text-left">
            <div>
              <label className="text-[10px] font-semibold tracking-widest uppercase text-foreground/25 mb-1.5 block">Creator Handle</label>
              <div className="relative">
                <span className="absolute left-4 top-1/2 -translate-y-1/2 text-sm text-foreground/30 font-body">@</span>
                <input
                  type="text"
                  value={handle}
                  onChange={e => { setHandle(e.target.value.replace(/[^a-zA-Z0-9._]/g, '').toLowerCase()); setError('') }}
                  placeholder="your_instagram_handle"
                  autoFocus
                  className="w-full border-2 border-border rounded-xl pl-8 pr-4 py-3.5 text-sm text-foreground placeholder:text-foreground/20 outline-none focus:border-foreground/25 transition-all font-body bg-background"
                />
              </div>
            </div>

            {error && (
              <div className="px-4 py-2.5 rounded-xl bg-red-50/50 border border-red-200/50">
                <p className="text-red-600 text-sm font-body">{error}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full font-display font-semibold text-[15px] py-3.5 rounded-xl bg-foreground text-background cursor-pointer hover:opacity-90 transition-all disabled:opacity-50"
            >
              {loading ? 'Setting up...' : 'Continue →'}
            </button>
          </form>

          <button
            onClick={handleSkip}
            className="mt-4 text-[13px] text-foreground/25 font-body hover:text-foreground/40 transition-colors cursor-pointer"
          >
            Skip for now
          </button>
        </div>
      </div>
    </div>
  )
}
