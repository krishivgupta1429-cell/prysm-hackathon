import { useNavigate } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { getUser, getToken, fetchUser, clearAuth, type User } from '@/lib/auth'

export default function Analyse() {
  const navigate = useNavigate()
  const [user, setUser] = useState<User | null>(getUser())

  useEffect(() => {
    const token = getToken()
    if (token) {
      fetchUser().then(u => { if (u) setUser(u) })
    }

    // Keyboard shortcuts
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === 'u' || e.key === 'U') navigate('/analyse/upload')
      if (e.key === 'l' || e.key === 'L') navigate('/analyse/link')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Nav */}
      <nav className="border-b border-border px-3 sm:px-8 h-14 flex items-center justify-between sticky top-0 bg-background/90 backdrop-blur-md z-50">
        <a href="/" className="font-logo text-[18px] sm:text-[21px] text-foreground">PRYSM</a>
        <div className="flex items-center gap-2 sm:gap-4">
          {user ? (
            <>
              <span className="text-xs sm:text-sm text-foreground/40 font-body">{user.credits} credits</span>
              <span className="text-xs sm:text-sm text-foreground/60 font-body hidden sm:inline">{user.username}</span>
              <button
                onClick={() => { clearAuth(); setUser(null); navigate('/') }}
                className="text-xs sm:text-sm text-foreground/30 hover:text-foreground/50 transition-colors cursor-pointer"
              >Logout</button>
            </>
          ) : (
            <a href="/login" className="text-xs sm:text-sm text-foreground/40 hover:text-foreground/60 transition-colors">Login</a>
          )}
          <a href="/dashboard" className="text-xs sm:text-sm text-foreground/40 hover:text-foreground/60 transition-colors">← Dashboard</a>
        </div>
      </nav>

      <div className="max-w-2xl mx-auto px-4 sm:px-6 py-10 sm:py-20">
        <div className="text-center mb-8 sm:mb-14" style={{ opacity: 0, animation: 'fade-slide-in 0.6s ease-out forwards' }}>
          <h1 className="font-display text-2xl sm:text-4xl font-bold tracking-tight mb-3">What do you want to analyse?</h1>
          <p className="text-foreground/35 font-body text-base">Pick a path — Prysm handles the rest</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          {/* Upload */}
          <button
            onClick={() => navigate('/analyse/upload')}
            className="group text-left relative border-2 border-border rounded-2xl p-8 bg-transparent cursor-pointer transition-all duration-300 hover:border-foreground/20 hover:-translate-y-1 hover:shadow-xl hover:shadow-foreground/[0.04] overflow-hidden"
            style={{ opacity: 0, animation: 'fade-slide-in 0.5s ease-out 150ms forwards' }}
          >
            {/* Top accent line on hover */}
            <div className="absolute top-0 left-0 w-full h-[2px] bg-green-500 opacity-0 group-hover:opacity-100 transition-opacity duration-300" />

            <div className="w-12 h-12 rounded-xl flex items-center justify-center mb-5 transition-all duration-300"
              style={{ background: 'rgba(34,197,94,0.08)', color: 'rgba(34,197,94,0.7)' }}>
              <svg className="w-6 h-6 group-hover:scale-110 transition-transform" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
              </svg>
            </div>

            <h2 className="font-display text-xl font-bold mb-2">Upload your video</h2>
            <p className="text-foreground/40 text-[14px] font-body leading-relaxed mb-6">
              Before you post — get it right. Upload your raw footage, draft, or edit and get a full breakdown.
            </p>

            <div className="flex items-center justify-between">
              <span className="text-[11px] text-foreground/20 font-body">MP4, MOV — up to 500MB</span>
              <div className="flex items-center gap-1.5 text-foreground/25 group-hover:text-foreground/50 transition-colors">
                <span className="text-[11px] font-body opacity-0 group-hover:opacity-100 transition-opacity">Start</span>
                <svg className="w-4 h-4 group-hover:translate-x-0.5 transition-transform" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
                </svg>
              </div>
            </div>

            {/* Keyboard hint */}
            <div className="absolute bottom-3 right-3 w-6 h-6 rounded-md border border-border flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
              <span className="text-[10px] text-foreground/20 font-body font-medium">U</span>
            </div>
          </button>

          {/* Link */}
          <button
            onClick={() => navigate('/analyse/link')}
            className="group text-left relative border-2 border-border rounded-2xl p-8 bg-transparent cursor-pointer transition-all duration-300 hover:border-foreground/20 hover:-translate-y-1 hover:shadow-xl hover:shadow-foreground/[0.04] overflow-hidden"
            style={{ opacity: 0, animation: 'fade-slide-in 0.5s ease-out 300ms forwards' }}
          >
            {/* Top accent line on hover */}
            <div className="absolute top-0 left-0 w-full h-[2px] bg-amber-500 opacity-0 group-hover:opacity-100 transition-opacity duration-300" />

            <div className="w-12 h-12 rounded-xl flex items-center justify-center mb-5 transition-all duration-300"
              style={{ background: 'rgba(245,158,11,0.08)', color: 'rgba(245,158,11,0.7)' }}>
              <svg className="w-6 h-6 group-hover:scale-110 transition-transform" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m13.35-.622l1.757-1.757a4.5 4.5 0 00-6.364-6.364l-4.5 4.5a4.5 4.5 0 001.242 7.244" />
              </svg>
            </div>

            <h2 className="font-display text-xl font-bold mb-2">Study a reel</h2>
            <p className="text-foreground/40 text-[14px] font-body leading-relaxed mb-6">
              Paste any Instagram Reel. Learn what makes it work — or find out what to do better next time.
            </p>

            <div className="flex items-center justify-between">
              <span className="text-[11px] text-foreground/20 font-body">Instagram Reels</span>
              <div className="flex items-center gap-1.5 text-foreground/25 group-hover:text-foreground/50 transition-colors">
                <span className="text-[11px] font-body opacity-0 group-hover:opacity-100 transition-opacity">Start</span>
                <svg className="w-4 h-4 group-hover:translate-x-0.5 transition-transform" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
                </svg>
              </div>
            </div>

            {/* Keyboard hint */}
            <div className="absolute bottom-3 right-3 w-6 h-6 rounded-md border border-border flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
              <span className="text-[10px] text-foreground/20 font-body font-medium">L</span>
            </div>
          </button>
        </div>

      </div>
    </div>
  )
}
