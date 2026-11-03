import { useEffect, useState, useCallback } from 'react'
import { ArrowRight, BarChart3, Sparkles, Zap, MessageSquare, Lightbulb, Search, PenTool } from 'lucide-react'

const cycleWords = ['Go Viral', 'Get Views', 'Trend', 'Get Shared']

const Hero = () => {
  const [loaded, setLoaded] = useState(false)
  const [wordIndex, setWordIndex] = useState(0)
  const [displayText, setDisplayText] = useState('')
  const [isDeleting, setIsDeleting] = useState(false)
  const [strikeActive, setStrikeActive] = useState(false)
  const [activePill, setActivePill] = useState<string | null>(null)

  useEffect(() => { const t = setTimeout(() => setLoaded(true), 60); return () => clearTimeout(t) }, [])

  // Listen for active pill highlight from FeatureDemos scroll
  useEffect(() => {
    const handler = (e: Event) => {
      const label = (e as CustomEvent).detail?.label ?? null
      setActivePill(label)
    }
    window.addEventListener('prysm:active-pill', handler)
    return () => window.removeEventListener('prysm:active-pill', handler)
  }, [])

  // Trigger strikethrough after load
  useEffect(() => {
    if (loaded) {
      const t = setTimeout(() => setStrikeActive(true), 800)
      return () => clearTimeout(t)
    }
  }, [loaded])

  // Typewriter effect
  const currentWord = cycleWords[wordIndex]

  const tick = useCallback(() => {
    if (isDeleting) {
      setDisplayText(prev => prev.slice(0, -1))
    } else {
      setDisplayText(currentWord.slice(0, displayText.length + 1))
    }
  }, [currentWord, displayText, isDeleting])

  useEffect(() => {
    if (!loaded) return

    // Initial delay before starting
    const startDelay = wordIndex === 0 && displayText === '' && !isDeleting ? 1200 : 0
    if (startDelay && displayText === '') {
      const t = setTimeout(() => setDisplayText(currentWord.slice(0, 1)), startDelay)
      return () => clearTimeout(t)
    }

    const speed = isDeleting ? 50 : 80

    if (!isDeleting && displayText === currentWord) {
      // Pause at complete word, then start deleting
      const t = setTimeout(() => setIsDeleting(true), 2000)
      return () => clearTimeout(t)
    }

    if (isDeleting && displayText === '') {
      // Move to next word, loop back to 0
      setIsDeleting(false)
      setWordIndex(prev => (prev + 1) % cycleWords.length)
      return
    }

    const t = setTimeout(tick, speed)
    return () => clearTimeout(t)
  }, [displayText, isDeleting, currentWord, wordIndex, loaded, tick])

  const fade = (d = 0) => ({
    opacity:   loaded ? 1 : 0,
    transform: loaded ? 'translateY(0)' : 'translateY(18px)',
    transition: `opacity 550ms ease-out ${d}ms, transform 550ms ease-out ${d}ms`,
  })

  const params = [
    { name: 'Call to Action',             score: 38, badge: 'CRITICAL',   badgeColor: 'bg-red-100/60 text-red-700 border-red-200/60' },
    { name: 'On-Screen Text',            score: 42, badge: 'NEEDS WORK', badgeColor: 'bg-amber-100/60 text-amber-700 border-amber-200/60' },
    { name: 'Save Potential',             score: 48, badge: 'NEEDS WORK', badgeColor: 'bg-amber-100/60 text-amber-700 border-amber-200/60' },
    { name: 'Shareability',              score: 62, badge: 'IMPROVE',    badgeColor: 'bg-amber-50/60 text-amber-600 border-amber-200/40' },
    { name: 'Trend Alignment',           score: 68, badge: 'IMPROVE',    badgeColor: 'bg-amber-50/60 text-amber-600 border-amber-200/40' },
    { name: 'Replay Value',              score: 71, badge: 'GOOD',       badgeColor: 'bg-green-50/60 text-green-600 border-green-200/40' },
    { name: 'Audio & Music',             score: 75, badge: 'GOOD',       badgeColor: 'bg-green-50/60 text-green-600 border-green-200/40' },
    { name: 'Visual Quality',            score: 76, badge: 'GOOD',       badgeColor: 'bg-green-50/60 text-green-600 border-green-200/40' },
    { name: 'Retention & Pacing',        score: 78, badge: 'GOOD',       badgeColor: 'bg-green-50/60 text-green-600 border-green-200/40' },
    { name: 'Hook & Opening',            score: 82, badge: 'GOOD',       badgeColor: 'bg-green-50/60 text-green-600 border-green-200/40' },
  ]

  const barColor = (s: number) => s >= 70 ? 'bg-green-500/70' : s >= 55 ? 'bg-amber-400/70' : 'bg-red-400/70'

  const featurePills = [
    { label: '10-Parameter Score', Icon: BarChart3, cardIndex: 0 },
    { label: 'Step-by-Step Fixes', Icon: Zap, cardIndex: 1 },
    { label: 'Caption Studio', Icon: PenTool, cardIndex: 2 },
    { label: 'Competitor Analysis', Icon: Sparkles, cardIndex: 3 },
    { label: 'Analyze Past Posts', Icon: Search, cardIndex: 4 },
    { label: 'Ask ARIA', Icon: MessageSquare, cardIndex: 5 },
    { label: 'Deep Insights', Icon: Lightbulb, cardIndex: 6 },
  ]

  const scrollTo = (id: string) => {
    const el = document.getElementById(id)
    if (el) window.dispatchEvent(new CustomEvent('lenis:scrollTo', { detail: { target: el, offset: -72 } }))
  }

  const scrollToPill = (cardIndex: number) => {
    // First scroll the page to the product section
    const section = document.getElementById('product')
    if (section) {
      window.dispatchEvent(new CustomEvent('lenis:scrollTo', { detail: { target: section, offset: -72 } }))
    }
    // Then scroll the horizontal card container to the right card
    setTimeout(() => {
      window.dispatchEvent(new CustomEvent('prysm:scroll-to-card', { detail: { index: cardIndex } }))
    }, 500)
  }

  return (
    <section className="relative overflow-hidden bg-background">
      {/* Animated gradient mesh */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        <div className="hero-gradient-orb absolute w-[280px] sm:w-[450px] md:w-[600px] h-[280px] sm:h-[450px] md:h-[600px] rounded-full opacity-30"
          style={{ top: '-15%', left: '20%', background: 'radial-gradient(circle, hsl(44 70% 85%) 0%, transparent 70%)', animation: 'float-orb-1 12s ease-in-out infinite' }} />
        <div className="hero-gradient-orb absolute w-[220px] sm:w-[380px] md:w-[500px] h-[220px] sm:h-[380px] md:h-[500px] rounded-full opacity-20"
          style={{ top: '-10%', right: '15%', background: 'radial-gradient(circle, hsl(38 60% 80%) 0%, transparent 70%)', animation: 'float-orb-2 15s ease-in-out infinite' }} />
        <div className="hero-gradient-orb absolute w-[200px] sm:w-[300px] md:w-[400px] h-[200px] sm:h-[300px] md:h-[400px] rounded-full opacity-15"
          style={{ top: '20%', left: '50%', transform: 'translateX(-50%)', background: 'radial-gradient(circle, hsl(44 50% 88%) 0%, transparent 70%)', animation: 'float-orb-3 10s ease-in-out infinite' }} />
      </div>
      <div className="absolute inset-0 dot-grid pointer-events-none opacity-30" />

      <div className="relative max-w-5xl mx-auto px-4 sm:px-6 pt-20 sm:pt-28 pb-3 sm:pb-6">

        {/* Badge */}
        <div className="flex justify-center mb-2.5 sm:mb-5" style={fade(0)}>
          <span className="tag-chip">
            <span className="w-1.5 h-1.5 rounded-full bg-green-500 flex-shrink-0 animate-pulse" />
            AI-powered content analysis
          </span>
        </div>

        {/* Headline — larger on mobile */}
        <div className="text-center" style={fade(80)}>
          <h1 className="hero-headline text-[clamp(2.4rem,7.5vw,5.5rem)] text-foreground leading-[0.93]">
            Know Why Your Reels
            <br />
            <span className="whitespace-nowrap">
              <span className="relative inline-block">
                <span className={`text-foreground/35 transition-all duration-500 ${strikeActive ? 'hero-strike' : ''}`}>Don't</span>
              </span>
              {' '}
              <span className="text-foreground/35">
                {displayText}
                <span className="inline-block w-[3px] h-[0.85em] bg-foreground/30 ml-0.5 align-middle animate-cursor-blink" />
              </span>
            </span>
          </h1>
        </div>

        {/* Subheadline */}
        <p className="mt-2.5 sm:mt-4 text-center text-[0.8125rem] sm:text-[1.0625rem] text-foreground/50 max-w-[48ch] mx-auto font-body leading-[1.5] px-2 sm:px-0" style={fade(160)}>
          Every frame analyzed. Every fix explained. Every trend checked.
        </p>

        {/* CTAs — primary + secondary */}
        <div className="mt-4 sm:mt-7 flex flex-col sm:flex-row items-center justify-center gap-2 sm:gap-3 px-2 sm:px-0" style={fade(240)}>
          <a href="/register"
            className="w-full sm:w-auto inline-flex items-center justify-center gap-2.5 px-8 sm:px-10 py-3 sm:py-4 rounded-full bg-foreground text-background font-body font-bold text-[0.875rem] sm:text-base hover:bg-foreground/90 transition-all duration-200 shadow-lg shadow-foreground/10 hover:shadow-xl hover:shadow-foreground/15 hover:-translate-y-0.5">
            Start for free <ArrowRight className="w-4 h-4" />
          </a>
          <button onClick={() => scrollTo('product')}
            className="w-full sm:w-auto inline-flex items-center justify-center gap-2 px-6 py-3 sm:py-4 rounded-full border border-border font-body font-semibold text-[0.875rem] sm:text-[0.9375rem] text-foreground/55 hover:text-foreground hover:border-foreground/25 hover:bg-secondary/40 transition-all duration-200">
            See how it works
          </button>
        </div>

        {/* Trust line */}
        <div className="mt-2 sm:mt-3 flex flex-col items-center gap-1.5" style={fade(300)}>
          <div className="flex items-center gap-2 sm:gap-3 text-[0.6875rem] sm:text-[0.8125rem] text-foreground/35 font-body">
            <span>No credit card required</span>
            <span className="w-px h-3 bg-border" />
            <span>Cancel anytime</span>
          </div>
        </div>

        {/* Feature pills — scrollable on mobile, centered on desktop */}
        <div className="mt-3 sm:mt-6 overflow-x-auto scrollbar-hide" style={fade(360)}>
          <div className="flex sm:flex-wrap sm:justify-center gap-2 px-4 sm:px-0 w-max sm:w-auto mx-auto">
            {featurePills.map(({ label, Icon, cardIndex }, i) => {
              const isActive = activePill === label
              return (
                <button key={label}
                  onClick={() => scrollToPill(cardIndex)}
                  className={`group inline-flex items-center gap-1.5 px-3 sm:px-2.5 py-2 sm:py-1.5 rounded-full border backdrop-blur-sm text-[11px] sm:text-[11px] font-body font-medium whitespace-nowrap cursor-pointer active:translate-y-0 transition-all duration-200 ${
                    isActive
                      ? 'border-foreground/25 bg-foreground/[0.06] text-foreground/80 shadow-sm'
                      : 'border-border/60 bg-background/80 text-foreground/50 hover:border-foreground/20 hover:text-foreground/70 hover:bg-secondary/60 hover:shadow-sm hover:-translate-y-0.5'
                  }`}
                  style={{ opacity: 0, animation: `fade-slide-in 0.4s ease-out ${400 + i * 80}ms forwards` }}>
                  <Icon className="w-3 h-3 flex-shrink-0 transition-transform duration-200 group-hover:scale-110" />
                  {label}
                </button>
              )
            })}
          </div>
        </div>
      </div>

      {/* ── Product Preview ── */}
      <div className="relative max-w-5xl mx-auto px-4 sm:px-6 pb-0 mt-1 sm:mt-4" style={fade(420)}>
        <div className="rounded-t-2xl border border-border border-b-0 bg-background overflow-hidden"
          style={{ boxShadow: '0 -4px 48px hsl(44 35% 55% / 0.12)' }}>

          {/* Browser chrome */}
          <div className="flex items-center gap-3 px-5 py-3 bg-secondary/50 border-b border-border">
            <div className="flex gap-1.5 flex-shrink-0">
              <div className="w-2.5 h-2.5 rounded-full bg-red-400/70" />
              <div className="w-2.5 h-2.5 rounded-full bg-amber-400/70" />
              <div className="w-2.5 h-2.5 rounded-full bg-green-400/70" />
            </div>
            <div className="flex-1 flex justify-center">
              <div className="h-5 rounded-md bg-background border border-border flex items-center px-3 gap-2 w-32 sm:w-48">
                <div className="w-1.5 h-1.5 rounded-full bg-foreground/20 flex-shrink-0" />
                <span className="text-[10px] text-foreground/38 font-body truncate">theprysm.io/results</span>
              </div>
            </div>
            <div className="hidden sm:flex items-center gap-1.5 flex-shrink-0">
              <span className="px-2.5 py-1 text-[11px] font-body font-medium rounded-full bg-background border border-border text-foreground/50">Dashboard</span>
              <span className="px-2.5 py-1 text-[11px] font-body font-semibold rounded-full bg-foreground text-background">New Analysis →</span>
            </div>
          </div>

          {/* Results layout */}
          <div className="flex flex-col md:flex-row min-h-[240px] sm:min-h-[420px]">

            {/* Left: frame viewer mockup */}
            <div className="hidden md:flex md:w-[42%] p-3 sm:p-5 flex-col gap-3">
              {/* Video frame with reel-style overlay */}
              <div className="relative rounded-xl overflow-hidden bg-foreground/5 aspect-[9/14]">
                <img src="/mockup-frame.jpg" alt="Prysm analysis example" className="w-full h-full object-cover" />
                {/* Duration badge */}
                <div className="absolute top-3 right-3 bg-foreground/60 backdrop-blur-sm text-background text-[10px] font-body font-semibold px-2 py-0.5 rounded-md">
                  0:28
                </div>
                {/* Bottom controls overlay */}
                <div className="absolute bottom-0 inset-x-0 bg-gradient-to-t from-foreground/70 to-transparent pt-10 pb-3 px-3">
                  <div className="flex items-center gap-2">
                    <div className="w-6 h-6 rounded-full bg-background/90 flex items-center justify-center flex-shrink-0">
                      <div className="w-0 h-0 border-l-[6px] border-l-foreground border-y-[4px] border-y-transparent ml-0.5" />
                    </div>
                    {/* Frame segments */}
                    <div className="flex-1 flex items-center gap-[3px]">
                      {Array.from({ length: 16 }).map((_, i) => (
                        <div key={i} className={`flex-1 h-[3px] rounded-full ${i < 4 ? 'bg-background/90' : 'bg-background/25'}`} />
                      ))}
                    </div>
                    <span className="text-[9px] text-background/80 font-body font-medium flex-shrink-0">4/16</span>
                  </div>
                </div>
              </div>

              {/* Post performance card */}
              <div className="border border-border rounded-xl p-3 bg-background">
                <p className="text-[8px] font-semibold tracking-widest uppercase text-foreground/30 mb-1.5">Post Performance</p>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <p className="text-sm font-display font-bold leading-none">2.1M</p>
                    <p className="text-[8px] text-foreground/35 font-body mt-0.5">Views</p>
                  </div>
                  <div>
                    <p className="text-sm font-display font-bold leading-none">184K</p>
                    <p className="text-[8px] text-foreground/35 font-body mt-0.5">Likes</p>
                  </div>
                </div>
                <p className="text-[11px] text-foreground/45 font-body font-semibold mt-2">@yourhandle</p>
              </div>
            </div>

            {/* Right: analysis results */}
            <div className="flex-1 p-3 sm:p-5 space-y-2 sm:space-y-3 md:border-l border-border/60 overflow-y-auto">

              {/* Score summary card */}
              <div className="rounded-xl border border-border p-4">
                <div className="flex items-start gap-4">
                  <div className="shrink-0">
                    <div className="relative" style={{ width: 64, height: 64 }}>
                      <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90">
                        <circle cx="50" cy="50" r="40" fill="none" stroke="hsl(44 42% 86%)" strokeWidth="8" />
                        <circle cx="50" cy="50" r="40" fill="none" stroke="hsl(142 50% 42%)" strokeWidth="8"
                          strokeLinecap="round" strokeDasharray="251" strokeDashoffset="73"
                          className="draw-ring-anim" style={{ '--offset': '73' } as React.CSSProperties} />
                      </svg>
                      <div className="absolute inset-0 flex items-center justify-center">
                        <span className="font-display text-xl font-bold text-foreground">71</span>
                      </div>
                    </div>
                  </div>
                  <div className="flex-1 min-w-0">
                    <span className="inline-block text-[8px] font-bold tracking-widest uppercase px-2 py-0.5 rounded-full bg-green-100/50 text-green-700/70 border border-green-200/40 mb-1.5">
                      Good Potential — Needs Polish
                    </span>
                    <p className="text-[11px] text-foreground/50 font-body leading-relaxed">
                      Your first 3 seconds have no text overlay — 68% of viewers watch on mute and scroll past without context.
                    </p>
                    <div className="flex items-center gap-2 mt-1.5">
                      <span className="text-[10px] text-foreground/25 font-body">After fixes →</span>
                      <span className="font-display font-bold text-[12px] text-green-600/80">84</span>
                      <span className="text-[10px] text-green-600/50 font-body">(+13)</span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Quick win */}
              <div className="bg-foreground/[0.025] border-l-[3px] border-green-500/50 rounded-r-xl p-3">
                <p className="text-[8px] font-semibold tracking-widest uppercase text-green-600/45 mb-0.5">Quick win</p>
                <p className="text-[10.5px] text-foreground/55 font-body leading-relaxed">
                  Add a bold text hook in the first frame — give mute viewers a reason to stop scrolling within 0.5 seconds.
                </p>
              </div>

              {/* Parameter breakdown */}
              <div className="rounded-xl border border-border p-4">
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <h3 className="font-display text-[12px] font-bold text-foreground">10-Parameter Breakdown</h3>
                    <p className="text-[9px] text-foreground/25 font-body">Based on Instagram Reels's actual ranking signals</p>
                  </div>
                </div>
                <div className="flex flex-col gap-2">
                  {params.map((p, i) => (
                    <div key={i} className="flex items-center gap-2"
                      style={{ opacity: 0, animation: `fade-slide-in 0.4s ease-out ${600 + i * 60}ms forwards` }}>
                      <span className="w-[65px] sm:w-[100px] shrink-0 text-[7px] sm:text-[9px] font-body text-foreground/45 truncate">{p.name}</span>
                      <div className="flex-1 h-1.5 bg-foreground/[0.04] rounded-full overflow-hidden">
                        <div className={`h-full rounded-full ${barColor(p.score)} bar-fill-anim`}
                          style={{ '--w': `${p.score}%` } as React.CSSProperties} />
                      </div>
                      <span className={`w-5 text-right text-[9px] font-bold font-display ${p.score >= 70 ? 'text-green-600/70' : p.score >= 55 ? 'text-amber-600/70' : 'text-red-500/70'}`}>{p.score}</span>
                      <span className={`text-[7px] font-bold tracking-wider uppercase px-1 py-0.5 rounded border shrink-0 ${p.badgeColor}`}>
                        {p.badge}
                      </span>
                    </div>
                  ))}
                </div>
              </div>

            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
export default Hero
