import { useEffect, useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { getToken, clearAuth, getUser } from '@/lib/auth'
import { apiUrl } from '@/lib/api'
import { canAccess, getNextPlan, getPlanCredits, getPlanLabel } from '@/lib/plans'
import { SUPPORT_EMAIL, ALLOW_LINKS } from '@/lib/config'
import { STUDIO_MODE } from '@/lib/config'


interface ParamSummary { name: string; score: number; problem: string }

interface AnalysisSummary {
  id: string; source_type: string; source_url: string | null; video_filename: string | null
  niche: string; platform: string; overall_score: number; score_label: string
  summary: string; brief: string; content_goal: string; intent: string
  top_priorities: string[]; parameters: ParamSummary[]; created_at: string
  has_feedback: boolean; post_uploader: string | null; post_views: number | null
  post_likes: number | null; thumbnail: string | null
}

interface DashboardData {
  user: { username: string; email: string; plan: string; credits: number }
  analyses: AnalysisSummary[]; total_analyses: number
  own_count: number; competitor_count: number; avg_score: number
}

/* ── Helpers ───────────────────────────────────────────────────────────── */

function formatDate(iso: string) {
  const d = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))
  if (diffDays === 0) return 'Today'
  if (diffDays === 1) return 'Yesterday'
  if (diffDays < 7) return `${diffDays}d ago`
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K'
  return String(n)
}

function scoreColor(score: number) {
  if (score >= 70) return 'text-green-600'
  if (score >= 50) return 'text-amber-600'
  return 'text-red-500'
}

function scoreBorderColor(score: number) {
  if (score >= 70) return 'border-l-green-500/60'
  if (score >= 50) return 'border-l-amber-400/60'
  return 'border-l-red-400/60'
}

function heatColor(score: number): string {
  if (score >= 80) return 'bg-green-500/70'
  if (score >= 70) return 'bg-green-400/50'
  if (score >= 60) return 'bg-amber-400/50'
  if (score >= 50) return 'bg-amber-500/50'
  if (score >= 40) return 'bg-red-300/50'
  return 'bg-red-400/60'
}

function isOwnVideo(a: AnalysisSummary): boolean {
  if (a.intent === 'competitor') return false
  if (a.intent === 'my_video') return true
  return a.source_type === 'upload'
}

function getGreeting(): string {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}

function getMotivation(avgScore: number, count: number, improving: boolean): string {
  if (count === 0) return "Let's analyse your first video"
  if (count === 1) return 'Upload more to start tracking your progress'
  if (improving && avgScore >= 70) return 'Your content keeps getting sharper'
  if (improving) return "You're on an upward trend — keep going"
  if (avgScore >= 70) return 'Consistently strong — now push for excellence'
  if (avgScore >= 50) return 'Solid foundation — the fixes are simple'
  return 'Every creator starts here — your next video will be better'
}

/* ── Scroll reveal hook ───────────────────────────────────────────────── */

function useReveal(ready: boolean = true) {
  const ref = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(false)
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    if (ref.current && ready && !mounted) setMounted(true)
  })

  useEffect(() => {
    if (!mounted) return
    const el = ref.current
    if (!el) return
    // Check if already in viewport
    const rect = el.getBoundingClientRect()
    if (rect.top < window.innerHeight + 100) {
      setTimeout(() => setVisible(true), 80)
      return
    }
    const obs = new IntersectionObserver(
      ([e]) => { if (e.isIntersecting) { setVisible(true); obs.unobserve(el) } },
      { threshold: 0.05 }
    )
    obs.observe(el)
    return () => obs.disconnect()
  }, [mounted])

  return { ref, className: `transition-all duration-700 ease-out ${visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-8'}` }
}

/* ── Animated Score Ring ──────────────────────────────────────────────── */

function ScoreRing({ score, size = 140, delay = 0, delta }: { score: number; size?: number; delay?: number; delta?: number }) {
  const [animated, setAnimated] = useState(0)
  useEffect(() => {
    const timer = setTimeout(() => {
      let current = 0
      const target = score
      const overshoot = Math.min(target + 6, 100)
      let phase: 'up' | 'over' | 'settle' = 'up'
      const interval = setInterval(() => {
        if (phase === 'up') { current += 2; if (current >= overshoot) { current = overshoot; phase = 'over' } }
        else if (phase === 'over') { current -= 0.5; if (current <= target) { current = target; phase = 'settle'; clearInterval(interval) } }
        setAnimated(Math.round(current))
      }, 16)
      return () => clearInterval(interval)
    }, delay)
    return () => clearTimeout(timer)
  }, [score, delay])

  const r = (size - 10) / 2
  const c = 2 * Math.PI * r
  const offset = c - (animated / 100) * c
  const tier = score >= 70 ? 'green' : score >= 50 ? 'amber' : 'red'
  const ringColor = tier === 'green' ? 'stroke-green-500' : tier === 'amber' ? 'stroke-amber-500' : 'stroke-red-400'
  const glowColor = tier === 'green' ? 'rgba(34,197,94,0.18)' : tier === 'amber' ? 'rgba(245,158,11,0.20)' : 'rgba(248,113,113,0.18)'
  const label = score >= 80 ? 'Excellent' : score >= 70 ? 'Strong' : score >= 60 ? 'Good potential' : score >= 50 ? 'Needs polish' : 'Needs work'
  const gradId = `score-grad-${tier}-${size}`

  return (
    <div className="relative" style={{ width: size, height: size }}>
      {/* Soft radial glow behind the ring */}
      <div
        className="absolute inset-0 rounded-full pointer-events-none"
        style={{
          background: `radial-gradient(circle at center, ${glowColor} 0%, transparent 65%)`,
          opacity: animated > 0 ? 1 : 0,
          transition: 'opacity 800ms ease-out',
        }}
      />
      <svg viewBox={`0 0 ${size} ${size}`} className="relative w-full h-full -rotate-90">
        <defs>
          <linearGradient id={gradId} x1="0%" y1="0%" x2="100%" y2="100%">
            {tier === 'green' ? (<>
              <stop offset="0%" stopColor="#34d399" /><stop offset="100%" stopColor="#16a34a" />
            </>) : tier === 'amber' ? (<>
              <stop offset="0%" stopColor="#fbbf24" /><stop offset="100%" stopColor="#f59e0b" />
            </>) : (<>
              <stop offset="0%" stopColor="#fb7185" /><stop offset="100%" stopColor="#ef4444" />
            </>)}
          </linearGradient>
        </defs>
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke="currentColor" strokeWidth="5" className="text-foreground/[0.06]" />
        <circle cx={size/2} cy={size/2} r={r} fill="none" strokeWidth="5" strokeLinecap="round"
          stroke={`url(#${gradId})`}
          className={`${ringColor} transition-all duration-100`}
          strokeDasharray={c} strokeDashoffset={offset} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-4xl font-display font-black tracking-tighter leading-none">{animated}</span>
        <span className="text-[9px] text-foreground/25 font-body mt-0.5">{label}</span>
        {delta !== undefined && delta !== 0 && (
          <span className={`text-[9px] font-body font-semibold mt-1 ${delta > 0 ? 'text-green-600/70' : 'text-red-500/70'}`}>
            {delta > 0 ? '↑' : '↓'} {Math.abs(delta)} from last
          </span>
        )}
      </div>
    </div>
  )
}

/* ── Lazy Thumbnail ───────────────────────────────────────────────────── */

function LazyThumbnail({ analysisId, token, size = 'md' }: { analysisId: string; token: string | null; size?: 'sm' | 'md' }) {
  const [src, setSrc] = useState<string | null>(null)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el || !token) return
    const obs = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) return
      obs.disconnect()
      fetch(apiUrl(`/prysm/dashboard/${analysisId}/thumbnail`), {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d?.thumbnail) setSrc(d.thumbnail) })
        .catch(() => {})
    }, { rootMargin: '200px' })
    obs.observe(el)
    return () => obs.disconnect()
  }, [analysisId, token])

  const dim = size === 'sm'
    ? 'w-10 h-[52px] rounded-md'
    : 'w-11 h-14 sm:w-14 sm:h-[72px] rounded-lg'

  return (
    <div ref={ref} className={`relative ${dim} overflow-hidden bg-foreground/5 shrink-0 shadow-sm ring-1 ring-foreground/[0.08] group-hover:ring-foreground/20 group-hover:shadow-md transition-all duration-300`}>
      {src ? (
        <img src={`data:image/jpeg;base64,${src}`} alt="" className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105" />
      ) : (
        <div className="w-full h-full animate-pulse bg-foreground/[0.06]" />
      )}
      <div className="absolute inset-0 ring-1 ring-inset ring-white/10 rounded-[inherit] pointer-events-none" />
    </div>
  )
}

/* ── Mini Score Ring ──────────────────────────────────────────────────── */

function MiniScoreRing({ score, size = 40 }: { score: number; size?: number }) {
  const r = (size - 5) / 2
  const c = 2 * Math.PI * r
  const offset = c - (score / 100) * c
  const color = score >= 70 ? 'stroke-green-500' : score >= 50 ? 'stroke-amber-500' : 'stroke-red-400'
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg viewBox={`0 0 ${size} ${size}`} className="w-full h-full -rotate-90">
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke="currentColor" strokeWidth="2.5" className="text-foreground/[0.04]" />
        <circle cx={size/2} cy={size/2} r={r} fill="none" strokeWidth="2.5" strokeLinecap="round"
          className={color} strokeDasharray={c} strokeDashoffset={offset} />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        <span className="text-[10px] font-display font-bold">{score}</span>
      </div>
    </div>
  )
}

/* ══════════════════════════════════════════════════════════════════════ */
/*  MAIN COMPONENT                                                      */
/* ══════════════════════════════════════════════════════════════════════ */

const PENDING_JOB_KEY = 'prysm_pending_job'

interface PendingJob { jobId: string; source: string; intent: string; startedAt: number }

export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [feedbackModal, setFeedbackModal] = useState<string | null>(null)
  const [feedbackForm, setFeedbackForm] = useState({ views: '', likes: '', comments: '', saves: '', shares: '', implemented: false })
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false)
  const [activeTab, setActiveTab] = useState<'my' | 'competitor'>('my')
  const [paramsExpanded, setParamsExpanded] = useState(false)
  const [pendingJob, setPendingJob] = useState<PendingJob | null>(null)
  const navigate = useNavigate()
  const token = getToken()

  // Scroll reveals — only activate after data loads
  const ready = !loading && !!data
  const heroReveal = useReveal(ready)
  const actionsReveal = useReveal(ready)
  const videosReveal = useReveal(ready)
  const insightsReveal = useReveal(ready)

  useEffect(() => {
    if (!token) { navigate('/login'); return }
    const user = getUser()
    if (user && user.is_verified === false) {
      navigate(`/verify-email?email=${encodeURIComponent(user.email || '')}`)
      return
    }
    fetchDashboard()

    // Check for in-flight analysis job from a previous page
    const raw = localStorage.getItem(PENDING_JOB_KEY)
    if (raw) {
      try {
        const job: PendingJob = JSON.parse(raw)
        const elapsed = Date.now() - job.startedAt
        if (job.jobId && elapsed < 10 * 60 * 1000) {
          setPendingJob(job)
          // Poll until complete, then navigate to results
          ;(async () => {
            const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}
            while (true) {
              await new Promise(r => setTimeout(r, 5000))
              try {
                const res = await fetch(apiUrl(`/prysm/analyse/status/${job.jobId}`), { headers })
                if (!res.ok) continue
                const d = await res.json()
                if (d.status === 'complete' && d.result) {
                  localStorage.setItem('prysm_result', JSON.stringify(d.result))
                  localStorage.setItem('prysm_video_source', job.source)
                  localStorage.setItem('prysm_intent', job.intent)
                  localStorage.removeItem(PENDING_JOB_KEY)
                  navigate('/results')
                  return
                }
                if (d.status === 'failed') {
                  localStorage.removeItem(PENDING_JOB_KEY)
                  setPendingJob(null)
                  return
                }
              } catch { /* retry */ }
            }
          })()
        } else {
          localStorage.removeItem(PENDING_JOB_KEY)
        }
      } catch {
        localStorage.removeItem(PENDING_JOB_KEY)
      }
    }
  }, [])

  function openGeneralChat() {
    localStorage.removeItem('prysm_chat_context')
    localStorage.removeItem('prysm_result')
    navigate('/chat?type=general')
  }

  async function fetchDashboard() {
    try {
      const res = await fetch(apiUrl('/prysm/dashboard'), { headers: { Authorization: `Bearer ${token}` } })
      if (res.status === 401) { clearAuth(); navigate('/login'); return }
      if (!res.ok) throw new Error('Failed to load dashboard')
      setData(await res.json())
    } catch (err: any) { setError(err.message) }
    finally { setLoading(false) }
  }

  async function viewAnalysis(a: AnalysisSummary) {
    try {
      const res = await fetch(apiUrl(`/prysm/dashboard/${a.id}`), { headers: { Authorization: `Bearer ${token}` } })
      if (!res.ok) throw new Error('Failed to load analysis')
      const result = await res.json()
      localStorage.setItem('prysm_result', JSON.stringify(result))
      localStorage.setItem('prysm_intent', result.intent || a.intent || 'my_video')
      localStorage.setItem('prysm_video_source', a.source_type === 'link' ? 'link' : 'upload')
      navigate('/results')
    } catch (err: any) { setError(err.message) }
  }

  async function submitFeedback(analysisId: string) {
    setFeedbackSubmitting(true)
    try {
      const res = await fetch(apiUrl(`/prysm/feedback/${analysisId}/submit`), {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          actual_views: feedbackForm.views ? parseInt(feedbackForm.views) : null,
          actual_likes: feedbackForm.likes ? parseInt(feedbackForm.likes) : null,
          actual_comments: feedbackForm.comments ? parseInt(feedbackForm.comments) : null,
          actual_saves: feedbackForm.saves ? parseInt(feedbackForm.saves) : null,
          actual_shares: feedbackForm.shares ? parseInt(feedbackForm.shares) : null,
          implemented_suggestions: feedbackForm.implemented,
          hours_after_posting: 48,
        }),
      })
      if (!res.ok) { const d = await res.json(); throw new Error(d.detail || 'Failed') }
      setFeedbackModal(null)
      setFeedbackForm({ views: '', likes: '', comments: '', saves: '', shares: '', implemented: false })
      fetchDashboard()
    } catch (err: any) { setError(err.message) }
    finally { setFeedbackSubmitting(false) }
  }

  if (loading) return (
    <div className="min-h-screen bg-background flex items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 border-2 border-foreground/10 border-t-foreground/40 rounded-full animate-spin" />
        <p className="text-foreground/25 font-body text-sm">Loading your dashboard...</p>
      </div>
    </div>
  )

  const userPlan = data?.user?.plan || getUser()?.plan
  const hasFullDashboard = canAccess.fullDashboard(userPlan)
  const nextPlan = getNextPlan(userPlan)

  const analyses = data?.analyses || []
  const hasAnalyses = analyses.length > 0
  const ownAnalyses = analyses.filter(isOwnVideo)
  const competitorAnalyses = analyses.filter(a => !isOwnVideo(a))
  const ownScores = ownAnalyses.map(a => a.overall_score).reverse()
  const improving = ownScores.length >= 2 && ownScores[ownScores.length - 1] > ownScores[0]

  // Parameter averages — OWN VIDEOS ONLY
  const paramAverages: Record<string, number> = {}
  const paramCounts: Record<string, number> = {}
  ownAnalyses.forEach(a => {
    a.parameters.forEach(p => {
      paramAverages[p.name] = (paramAverages[p.name] || 0) + p.score
      paramCounts[p.name] = (paramCounts[p.name] || 0) + 1
    })
  })
  Object.keys(paramAverages).forEach(k => { paramAverages[k] = Math.round(paramAverages[k] / (paramCounts[k] || 1)) })
  const sortedParams = Object.entries(paramAverages).sort((a, b) => a[1] - b[1])
  const weakestParam = sortedParams[0]
  const strongestParam = sortedParams[sortedParams.length - 1]

  // Recurring issues — OWN VIDEOS ONLY
  const problemCounts: Record<string, { count: number; param: string; avgScore: number; latestProblem: string }> = {}
  ownAnalyses.forEach(a => {
    a.parameters.forEach(p => {
      if (p.score < 60 && p.problem) {
        if (!problemCounts[p.name]) problemCounts[p.name] = { count: 0, param: p.name, avgScore: 0, latestProblem: p.problem }
        problemCounts[p.name].count++
        problemCounts[p.name].avgScore += p.score
        problemCounts[p.name].latestProblem = p.problem
      }
    })
  })
  Object.values(problemCounts).forEach(v => { v.avgScore = Math.round(v.avgScore / v.count) })
  const recurringIssues = Object.values(problemCounts).sort((a, b) => b.count - a.count).slice(0, 3)

  // Best vs Latest
  const bestVideo = ownAnalyses.length > 0 ? ownAnalyses.reduce((best, a) => a.overall_score > best.overall_score ? a : best) : null
  const latestVideo = ownAnalyses[0] || null
  const showComparison = bestVideo && latestVideo && bestVideo.id !== latestVideo.id

  // Days since last analysis
  const daysSince = ownAnalyses.length > 0
    ? Math.floor((Date.now() - new Date(ownAnalyses[0].created_at).getTime()) / (1000 * 60 * 60 * 24))
    : -1

  return (
    <div className="min-h-screen bg-background text-foreground relative">
      {/* Subtle film-grain overlay for warmth */}
      <div className="grain-overlay" aria-hidden="true" />
      {/* Nav */}
      <nav className="border-b border-border px-3 sm:px-8 h-14 flex items-center justify-between sticky top-0 bg-background/90 backdrop-blur-md z-50">
        <a href="/" className="font-logo text-[18px] sm:text-[21px] text-foreground">PRYSM</a>
        <div className="flex items-center gap-2 sm:gap-4">
          <a href="/analyse" className="text-xs sm:text-sm text-foreground/40 hover:text-foreground/60 transition-colors hidden sm:block">New Analysis</a>
          {!STUDIO_MODE && (() => {
            const credits = data?.user.credits ?? 0
            const maxCredits = Math.max(getPlanCredits(userPlan), credits)
            const pct = Math.min((credits / maxCredits) * 100, 100)
            const isLow = credits < 10
            const isExhausted = credits <= 0
            const planLabel = getPlanLabel(userPlan)
            return isExhausted ? (
              <a href={STUDIO_MODE ? undefined : '/pricing'} className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-red-50 border border-red-200/40 hover:bg-red-100/60 transition-colors">
                <span className="text-[10px] sm:text-[11px] text-red-600/70 font-body font-semibold">0 credits{STUDIO_MODE ? '' : ' — Upgrade'}</span>
              </a>
            ) : (
              <div className="relative group/credits">
                <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full border cursor-default ${isLow ? 'border-amber-300/40 bg-amber-50/30' : 'border-border'}`}>
                  <div className="w-8 sm:w-12 h-1.5 bg-foreground/[0.06] rounded-full overflow-hidden">
                    <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: isLow ? '#f59e0b' : 'linear-gradient(90deg, #f59e0b, #22c55e)' }} />
                  </div>
                  <span className={`text-[10px] sm:text-[11px] font-body font-medium ${isLow ? 'text-amber-600/70' : 'text-foreground/35'}`}>{Math.round(credits * 10) / 10}</span>
                </div>
                {/* Tooltip */}
                <div className="absolute right-0 top-full mt-2 opacity-0 group-hover/credits:opacity-100 pointer-events-none group-hover/credits:pointer-events-auto transition-opacity duration-200 z-50 w-[200px]">
                  <div className="bg-foreground text-background rounded-xl p-3 shadow-xl">
                    <div className="flex items-center justify-between mb-2">
                      <p className="text-[10px] font-display font-bold opacity-90">{planLabel} plan</p>
                      <p className="text-[10px] font-body opacity-50">{Math.round(credits * 10) / 10} / {maxCredits}</p>
                    </div>
                    <div className="w-full h-1 bg-background/15 rounded-full overflow-hidden mb-2">
                      <div className="h-full rounded-full" style={{ width: `${pct}%`, background: isLow ? '#fbbf24' : 'linear-gradient(90deg, #fbbf24, #4ade80)' }} />
                    </div>
                    {!STUDIO_MODE && userPlan && userPlan.toLowerCase() !== 'creator' && (
                      <a href="/pricing" className="block mt-1 text-[10px] font-body font-semibold opacity-90 hover:opacity-100 transition-opacity">
                        Upgrade plan →
                      </a>
                    )}
                  </div>
                </div>
              </div>
            )
          })()}
          <a href="/settings" className="text-foreground/30 hover:text-foreground/50 transition-colors" title="Settings">
            <svg className="w-[18px] h-[18px]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
            </svg>
          </a>
          <button onClick={() => { clearAuth(); navigate('/') }}
            className="text-xs sm:text-sm text-foreground/30 hover:text-foreground/50 transition-colors cursor-pointer">Logout</button>
        </div>
      </nav>

      {/* ═══ HERO SECTION ═══ */}
      <div ref={heroReveal.ref} className={heroReveal.className}>
        <div className="pb-8 sm:pb-10 pt-6 sm:pt-8 px-4 sm:px-6 bg-background">
          <div className="max-w-6xl mx-auto">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
              <div className="flex-1">
                <h1 className="font-display text-2xl sm:text-4xl font-black tracking-tight mb-1 sm:mb-2">
                  {getGreeting()}, @{data?.user.username || 'creator'}
                </h1>
                <p className="text-foreground/35 text-sm sm:text-base font-body mb-3">
                  {getMotivation(data?.avg_score || 0, ownAnalyses.length, improving)}
                </p>
                <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
                  <span className="text-[10px] sm:text-[11px] text-foreground/30 font-body px-2 sm:px-2.5 py-1 rounded-full border border-border">
                    {ownAnalyses.length} video{ownAnalyses.length !== 1 ? 's' : ''} analysed
                  </span>
                  {competitorAnalyses.length > 0 && (
                    <span className="text-[10px] sm:text-[11px] text-foreground/30 font-body px-2 sm:px-2.5 py-1 rounded-full border border-border">
                      {competitorAnalyses.length} competitor{competitorAnalyses.length !== 1 ? 's' : ''} studied
                    </span>
                  )}
                  {daysSince >= 0 && (
                    <span className={`text-[10px] sm:text-[11px] font-body px-2 sm:px-2.5 py-1 rounded-full border ${daysSince > 3 ? 'text-amber-600/60 border-amber-300/30' : 'text-foreground/30 border-border'}`}>
                      {daysSince === 0 ? 'Analysed today' : daysSince === 1 ? 'Yesterday' : `${daysSince}d ago`}
                    </span>
                  )}
                </div>
              </div>

              {/* Big animated score ring */}
              {ownAnalyses.length > 0 && (
                <div className="flex flex-col items-center">
                  <ScoreRing
                    score={data?.avg_score || 0}
                    size={120}
                    delay={300}
                    delta={ownScores.length >= 2 ? ownScores[ownScores.length - 1] - ownScores[ownScores.length - 2] : undefined}
                  />
                  <p className="text-[9px] text-foreground/20 font-body mt-2 tracking-wider uppercase">Your avg score</p>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {error && <p className="text-red-500 text-sm mb-4 max-w-6xl mx-auto px-6 mt-4">{error}</p>}

      {/* ═══ QUICK ACTIONS — overlapping the dark/light transition ═══ */}
      <div ref={actionsReveal.ref} className={actionsReveal.className}>
        <div className="max-w-6xl mx-auto px-4 sm:px-6 -mt-1">
          <div className={`grid ${ALLOW_LINKS ? 'grid-cols-3' : 'grid-cols-2'} gap-2 sm:gap-3 mb-6 sm:mb-8`}>
            {[
              { href: '/analyse/upload', label: 'Upload video', desc: 'Analyse before you post', icon: 'M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5', accent: '#22c55e' },
              { href: '/analyse/link', label: 'Paste a link', desc: 'Study any Instagram reel', icon: 'M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m13.35-.622l1.757-1.757a4.5 4.5 0 00-6.364-6.364l-4.5 4.5a4.5 4.5 0 001.242 7.244', accent: '#f59e0b' },
              { href: null, label: 'Chat with Prysm', desc: 'Ask anything about your content', icon: 'M8.625 12a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H8.25m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H12m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 01-2.555-.337A5.972 5.972 0 015.41 20.97a5.969 5.969 0 01-.474-.065 4.48 4.48 0 00.978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25z', accent: '#8b5cf6' },
            ].filter(a => ALLOW_LINKS || a.href !== '/analyse/link').map((a, i) => (
              <a key={i} href={a.href ?? undefined} onClick={!a.href ? (e) => { e.preventDefault(); openGeneralChat() } : undefined}
                className="group relative rounded-xl sm:rounded-2xl p-3 sm:p-5 transition-all duration-300 cursor-pointer hover:-translate-y-1 bg-background border border-border overflow-hidden"
                style={{
                  opacity: 0,
                  animation: `fade-slide-in 0.5s ease-out ${200 + i * 100}ms forwards`,
                  // @ts-ignore — used by inline hover styles
                  '--accent': a.accent,
                } as React.CSSProperties}
                onMouseEnter={(e) => { e.currentTarget.style.boxShadow = `0 12px 32px ${a.accent}1a, 0 2px 8px ${a.accent}10`; e.currentTarget.style.borderColor = `${a.accent}40` }}
                onMouseLeave={(e) => { e.currentTarget.style.boxShadow = ''; e.currentTarget.style.borderColor = '' }}
              >
                {/* Top accent bar — slides in from left on hover */}
                <div className="absolute top-0 left-0 h-[2px] w-0 group-hover:w-full transition-all duration-500 ease-out" style={{ background: a.accent }} />
                {/* Subtle radial wash that appears on hover */}
                <div className="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none"
                  style={{ background: `radial-gradient(120% 80% at 100% 0%, ${a.accent}10 0%, transparent 60%)` }} />

                <div className="relative">
                  <div className="w-8 h-8 sm:w-10 sm:h-10 rounded-lg sm:rounded-xl flex items-center justify-center mb-2 sm:mb-3 transition-all duration-300 group-hover:scale-105"
                    style={{ background: `${a.accent}10`, color: `${a.accent}90` }}>
                    <svg className="w-4 h-4 sm:w-5 sm:h-5 transition-transform duration-300 group-hover:scale-110 group-hover:rotate-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d={a.icon} />
                    </svg>
                  </div>
                  <div className="flex items-center justify-between">
                    <p className="font-display font-semibold text-xs sm:text-sm mb-0.5">{a.label}</p>
                    {/* Arrow that slides in on hover */}
                    <svg className="w-3.5 h-3.5 -translate-x-2 opacity-0 group-hover:translate-x-0 group-hover:opacity-100 transition-all duration-300 hidden sm:block"
                      style={{ color: a.accent }}
                      fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
                    </svg>
                  </div>
                  <p className="text-[10px] sm:text-[11px] text-foreground/25 font-body hidden sm:block">{a.desc}</p>
                </div>
              </a>
            ))}
          </div>
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-4 sm:px-6">

        {/* ═══ IN-PROGRESS CARD ═══ */}
        {pendingJob && (
          <div className="mb-6 border border-foreground/10 rounded-2xl p-5 flex items-center gap-4 bg-foreground/[0.02]"
            style={{ animation: 'fade-slide-in 0.4s ease-out' }}>
            <div className="w-10 h-10 rounded-xl bg-foreground/[0.06] flex items-center justify-center shrink-0">
              <div className="w-5 h-5 border-2 border-foreground/15 border-t-foreground/50 rounded-full animate-spin" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-[13px] font-display font-semibold text-foreground/70">Analysis in progress</p>
              <p className="text-[11px] text-foreground/35 font-body mt-0.5">
                {pendingJob.source === 'link' ? 'Breaking down the reel' : 'Analysing your video'} — we'll take you to results when it's ready
              </p>
            </div>
            <button
              onClick={() => { localStorage.removeItem(PENDING_JOB_KEY); setPendingJob(null) }}
              className="text-[11px] text-foreground/25 hover:text-foreground/50 font-body cursor-pointer transition-colors shrink-0"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* ═══ EMPTY STATE ═══ */}
        {!hasAnalyses && (
          <div className="border border-border rounded-2xl p-10 sm:p-20 text-center">
            <div className="w-16 h-16 rounded-2xl bg-foreground/[0.03] flex items-center justify-center mx-auto mb-6">
              <svg className="w-8 h-8 text-foreground/15" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 10.5l4.72-4.72a.75.75 0 011.28.53v11.38a.75.75 0 01-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 002.25-2.25v-9a2.25 2.25 0 00-2.25-2.25h-9A2.25 2.25 0 002.25 7.5v9a2.25 2.25 0 002.25 2.25z" />
              </svg>
            </div>
            <p className="text-foreground/40 text-base font-body mb-1">No analyses yet</p>
            <p className="text-foreground/20 text-sm font-body mb-6">{ALLOW_LINKS ? 'Upload a video or paste a link to get started' : 'Upload a video to get started'}</p>
            <a href="/analyse" className="inline-block px-8 py-3.5 rounded-xl bg-foreground text-background text-sm font-display font-semibold hover:opacity-90 transition-opacity">
              Run your first analysis →
            </a>
          </div>
        )}

        {/* ═══ MAIN CONTENT ═══ */}
        {hasAnalyses && (
          <>
            {/* ── Coach Insight Banner ── */}
            {weakestParam && strongestParam && ownAnalyses.length >= 3 && hasFullDashboard && (() => {
              const latestScore = ownScores[ownScores.length - 1]
              const firstScore = ownScores[0]
              const scoreDelta = latestScore - firstScore
              const bestScore = bestVideo?.overall_score ?? latestScore
              const latestVsBest = latestScore - bestScore
              const avgScore = data?.avg_score ?? 0
              const topIssue = recurringIssues[0]
              const topIssue2 = recurringIssues[1]

              // Build 3 sentences: trend + strength/weakness + specific action
              // Sentence 1: trajectory
              let sentence1 = ''
              if (scoreDelta >= 10) sentence1 = `You've improved ${scoreDelta} points across your last ${ownScores.length} videos — your content strategy is clicking.`
              else if (scoreDelta >= 5) sentence1 = `You're trending up ${scoreDelta} points across ${ownScores.length} videos — steady progress.`
              else if (scoreDelta <= -5) sentence1 = `Your scores have dipped ${Math.abs(scoreDelta)} points recently — worth reviewing what changed.`
              else if (latestVsBest <= -10) sentence1 = `Your best was ${bestScore} but your latest is ${latestScore} — you've done it before, you can get back there.`
              else sentence1 = `You're averaging ${Math.round(avgScore)}/100 across ${ownScores.length} videos — a consistent baseline to build from.`

              // Sentence 2: strength + weakness
              const sentence2 = `Your strongest signal is <strong>${strongestParam[0]}</strong> at <score color="${scoreColor(strongestParam[1])}">${strongestParam[1]}</score> — lean into that. Your biggest drag is <strong>${weakestParam[0]}</strong> at <score color="${scoreColor(weakestParam[1])}">${weakestParam[1]}</score>${topIssue2 ? ` and <strong>${topIssue2.param}</strong> at <score color="${scoreColor(topIssue2.avgScore)}">${topIssue2.avgScore}</score>` : ''}.`

              // Sentence 3: specific action from the most recurring issue
              let sentence3 = ''
              if (topIssue && topIssue.latestProblem) {
                const problem = topIssue.latestProblem.replace(/\.$/, '')
                sentence3 = `Across ${topIssue.count} video${topIssue.count > 1 ? 's' : ''}, <strong>${topIssue.param}</strong> keeps coming up: ${problem}.`
              }

              return (
              <div className="mb-8 rounded-2xl p-5 overflow-hidden relative border border-border bg-foreground/[0.02]"
                style={{ opacity: 0, animation: 'fade-slide-in 0.6s ease-out 400ms forwards' }}>
                <div className="absolute top-0 left-0 w-1 h-full rounded-l-2xl" style={{ background: 'linear-gradient(180deg, #f59e0b 0%, #22c55e 100%)' }} />
                <div className="flex items-start gap-4 pl-3">
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-0.5 bg-amber-100/60">
                    <svg className="w-4 h-4 text-amber-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 18v-5.25m0 0a6.01 6.01 0 001.5-.189m-1.5.189a6.01 6.01 0 01-1.5-.189m3.75 7.478a12.06 12.06 0 01-4.5 0m3.75 2.383a14.406 14.406 0 01-3 0M14.25 18v-.192c0-.983.658-1.823 1.508-2.316a7.5 7.5 0 10-7.517 0c.85.493 1.509 1.333 1.509 2.316V18" />
                    </svg>
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-[10px] font-semibold tracking-widest uppercase text-amber-700/50 mb-1.5">Prysm coach</p>
                    <p className="text-[14px] text-foreground/55 font-body leading-relaxed mb-1">
                      {sentence1}
                    </p>
                    <p className="text-[14px] text-foreground/55 font-body leading-relaxed mb-1">
                      Your strongest signal is <span className="font-semibold text-foreground/80">{strongestParam[0]}</span> at <span className={`font-bold ${scoreColor(strongestParam[1])}`}>{strongestParam[1]}</span> — lean into that. Your biggest drag is <span className="font-semibold text-foreground/80">{weakestParam[0]}</span> at <span className={`font-bold ${scoreColor(weakestParam[1])}`}>{weakestParam[1]}</span>{topIssue2 ? <> and <span className="font-semibold text-foreground/80">{topIssue2.param}</span> at <span className={`font-bold ${scoreColor(topIssue2.avgScore)}`}>{topIssue2.avgScore}</span></> : ''}.
                    </p>
                    {sentence3 && (
                      <p className="text-[14px] text-foreground/40 font-body leading-relaxed">
                        Across <span className="font-semibold text-foreground/60">{topIssue.count} video{topIssue.count > 1 ? 's' : ''}</span>, <span className="font-semibold text-foreground/60">{topIssue.param}</span> keeps coming up: {topIssue.latestProblem.replace(/\.$/, '')}.
                      </p>
                    )}
                  </div>
                </div>
              </div>
              )
            })()}

            <div className="flex gap-4 sm:gap-6 lg:flex-row flex-col">

              {/* ─── LEFT: My Videos ─── */}
              <div ref={videosReveal.ref} className={`flex-1 min-w-0 ${videosReveal.className}`}>

                {/* Best vs Latest comparison */}
                {showComparison && activeTab === 'my' && hasFullDashboard && (
                  <div className="mb-6 border border-border rounded-2xl overflow-hidden">
                    <div className="px-5 py-3 border-b border-border bg-foreground/[0.02]">
                      <p className="text-[9px] font-semibold tracking-widest uppercase text-foreground/25">Your best vs latest</p>
                    </div>
                    <div className="grid grid-cols-2 divide-x divide-border">
                      <div className="p-5 text-center">
                        <p className="text-[10px] text-foreground/25 font-body mb-2">Best</p>
                        <p className={`text-3xl font-display font-black ${scoreColor(bestVideo!.overall_score)}`}>{bestVideo!.overall_score}</p>
                        <p className="text-[11px] text-foreground/40 font-body mt-1 line-clamp-1">{bestVideo!.brief}</p>
                        <p className="text-[9px] text-foreground/20 font-body mt-0.5">{formatDate(bestVideo!.created_at)}</p>
                      </div>
                      <div className="p-5 text-center">
                        <p className="text-[10px] text-foreground/25 font-body mb-2">Latest</p>
                        <p className={`text-3xl font-display font-black ${scoreColor(latestVideo!.overall_score)}`}>{latestVideo!.overall_score}</p>
                        <p className="text-[11px] text-foreground/40 font-body mt-1 line-clamp-1">{latestVideo!.brief}</p>
                        <p className="text-[9px] text-foreground/20 font-body mt-0.5">{formatDate(latestVideo!.created_at)}</p>
                      </div>
                    </div>
                    {latestVideo!.overall_score >= bestVideo!.overall_score && (
                      <div className="px-5 py-2.5 border-t border-border bg-green-50/30">
                        <p className="text-[11px] text-green-600/70 font-body text-center">Your latest video is your best yet</p>
                      </div>
                    )}
                  </div>
                )}

                {/* Tabs: My Videos / Reel Breakdowns */}
                <div className="flex items-center gap-1 mb-5 border-b border-border">
                  <button
                    onClick={() => setActiveTab('my')}
                    className={`px-4 py-2.5 text-sm font-display font-semibold transition-all relative cursor-pointer ${
                      activeTab === 'my'
                        ? 'text-foreground/80'
                        : 'text-foreground/30 hover:text-foreground/50'
                    }`}
                  >
                    My Videos
                    {ownAnalyses.length > 0 && (
                      <span className="ml-1.5 text-[10px] text-foreground/20 font-body">{ownAnalyses.length}</span>
                    )}
                    {activeTab === 'my' && (
                      <div className="absolute bottom-0 left-0 w-full h-[2px] bg-foreground/60 rounded-full" />
                    )}
                  </button>
                  {ALLOW_LINKS && <button
                    onClick={() => setActiveTab('competitor')}
                    className={`px-4 py-2.5 text-sm font-display font-semibold transition-all relative cursor-pointer ${
                      activeTab === 'competitor'
                        ? 'text-foreground/80'
                        : 'text-foreground/30 hover:text-foreground/50'
                    }`}
                  >
                    Reel Breakdowns
                    {competitorAnalyses.length > 0 && (
                      <span className="ml-1.5 text-[10px] text-foreground/20 font-body">{competitorAnalyses.length}</span>
                    )}
                    {activeTab === 'competitor' && (
                      <div className="absolute bottom-0 left-0 w-full h-[2px] bg-foreground/60 rounded-full" />
                    )}
                  </button>}
                </div>

                {/* My Videos tab content */}
                {activeTab === 'my' && ownAnalyses.length === 0 && (
                  <div className="border border-border rounded-xl p-10 text-center mb-4">
                    <p className="text-foreground/30 text-sm font-body mb-2">No own videos analysed yet</p>
                    <a href="/analyse/upload" className="text-sm text-foreground/50 underline hover:text-foreground/70 font-body">Upload your first video →</a>
                  </div>
                )}

                {activeTab === 'my' && <div className="flex flex-col gap-3">
                  {ownAnalyses.map((a, idx) => (
                    <div key={a.id}
                      className={`border border-border border-l-[3px] ${scoreBorderColor(a.overall_score)} rounded-xl overflow-hidden hover:border-foreground/15 transition-all cursor-pointer group hover:shadow-md hover:shadow-foreground/[0.02] hover:-translate-y-0.5`}
                      onClick={() => viewAnalysis(a)}
                      style={{ opacity: 0, animation: `fade-slide-in 0.5s ease-out ${idx * 80}ms forwards` }}>
                      <div className="flex items-center gap-3 sm:gap-4 p-3 sm:p-4">
                        {/* Thumbnail — lazy loaded */}
                        <LazyThumbnail analysisId={a.id} token={token} size="md" />

                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-1.5 sm:gap-2 mb-1 flex-wrap">
                            <span className={`text-sm sm:text-base font-display font-bold ${scoreColor(a.overall_score)}`}>{a.overall_score}</span>
                            <span className="text-[8px] sm:text-[9px] px-1.5 sm:px-2 py-0.5 rounded-full border border-border text-foreground/25 font-semibold uppercase tracking-wider">
                              {a.score_label}
                            </span>
                            <span className="text-[8px] sm:text-[9px] px-1.5 py-0.5 rounded-full bg-secondary/80 text-foreground/20 hidden sm:inline">
                              {a.source_type === 'link' ? 'Link' : 'Upload'}
                            </span>
                            {a.post_uploader && (
                              <span className="text-[9px] text-foreground/20 font-body hidden sm:inline">@{a.post_uploader}</span>
                            )}
                          </div>
                          <p className="text-[12px] sm:text-[13px] text-foreground/50 font-body line-clamp-1 mb-1">{a.brief || a.summary}</p>
                          <div className="flex items-center gap-2 text-[9px] text-foreground/18 font-body">
                            <span>{a.niche}</span>
                            <span>·</span>
                            <span>{formatDate(a.created_at)}</span>
                          </div>
                        </div>

                        {/* Actions */}
                        <div className="flex items-center gap-2 shrink-0">
                          {/* Feedback button — always visible */}
                          <button
                            onClick={e => {
                              e.stopPropagation()
                              const subject = encodeURIComponent(`Prysm Analysis Feedback — Job #${a.id.slice(0, 8)} (Score: ${a.overall_score})`)
                              const body = encodeURIComponent(`Hi Prysm team,\n\nI'd like to report an issue with my analysis:\n\nAnalysis ID: ${a.id}\nScore received: ${a.overall_score}\nNiche: ${a.niche}\n\nIssue description:\n[Please describe the issue here]\n\nThank you`)
                              window.open(`mailto:${SUPPORT_EMAIL}?subject=${subject}&body=${body}`, '_blank')
                            }}
                            title="Report an issue with this analysis"
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: '5px',
                              padding: '5px 10px',
                              borderRadius: '6px',
                              border: '1px solid rgba(239,68,68,0.35)',
                              background: 'rgba(239,68,68,0.08)',
                              color: 'rgba(239,68,68,0.8)',
                              fontSize: '11px',
                              fontWeight: 600,
                              cursor: 'pointer',
                              whiteSpace: 'nowrap',
                              transition: 'all 0.15s',
                            }}
                            onMouseEnter={e => {
                              (e.currentTarget as HTMLButtonElement).style.background = 'rgba(239,68,68,0.16)'
                              ;(e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(239,68,68,0.6)'
                            }}
                            onMouseLeave={e => {
                              (e.currentTarget as HTMLButtonElement).style.background = 'rgba(239,68,68,0.08)'
                              ;(e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(239,68,68,0.35)'
                            }}
                          >
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/>
                              <line x1="4" y1="22" x2="4" y2="15"/>
                            </svg>
                            Report Issue
                          </button>
                          <span className="text-[10px] text-foreground/20 font-body sm:opacity-0 sm:group-hover:opacity-100 transition-all duration-200">View →</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>}

                {/* Reel Breakdowns tab content */}
                {activeTab === 'competitor' && competitorAnalyses.length === 0 && (
                  <div className="border border-border rounded-xl p-10 text-center mb-4">
                    <p className="text-foreground/30 text-sm font-body mb-2">No reel breakdowns yet</p>
                    <a href="/analyse/link" className="text-sm text-foreground/50 underline hover:text-foreground/70 font-body">Paste a competitor's reel link →</a>
                  </div>
                )}

                {activeTab === 'competitor' && competitorAnalyses.length > 0 && (
                  <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
                    {competitorAnalyses.map((a, i) => (
                      <div key={a.id}
                        className="border border-border rounded-xl p-4 hover:border-foreground/15 transition-all cursor-pointer group hover:shadow-md hover:shadow-foreground/[0.02] hover:-translate-y-0.5"
                        onClick={() => viewAnalysis(a)}
                        style={{ opacity: 0, animation: `fade-slide-in 0.4s ease-out ${i * 60}ms forwards` }}>
                        <div className="flex items-center gap-2.5 mb-2.5">
                          <LazyThumbnail analysisId={a.id} token={token} size="sm" />
                          <div className="min-w-0 flex-1">
                            <p className="text-[11px] font-display font-semibold truncate">@{a.post_uploader || 'unknown'}</p>
                            <p className="text-[9px] text-foreground/20 font-body">{a.niche} · {formatDate(a.created_at)}</p>
                          </div>
                          <span className={`text-base font-display font-bold ${scoreColor(a.overall_score)}`}>{a.overall_score}</span>
                        </div>
                        <p className="text-[11px] text-foreground/35 font-body line-clamp-2">{a.brief || a.summary}</p>
                        {a.post_views != null && a.post_views > 0 && (
                          <p className="text-[9px] text-foreground/15 font-body mt-2">{formatNumber(a.post_views)} views · {formatNumber(a.post_likes || 0)} likes</p>
                        )}
                        <button
                          onClick={e => {
                            e.stopPropagation()
                            const subject = encodeURIComponent(`Prysm Analysis Feedback — Job #${a.id.slice(0, 8)} (Score: ${a.overall_score})`)
                            const body = encodeURIComponent(`Hi Prysm team,\n\nI'd like to report an issue with my analysis:\n\nAnalysis ID: ${a.id}\nScore received: ${a.overall_score}\nNiche: ${a.niche}\n\nIssue description:\n[Please describe the issue here]\n\nThank you`)
                            window.open(`mailto:${SUPPORT_EMAIL}?subject=${subject}&body=${body}`, '_blank')
                          }}
                          title="Report an issue with this analysis"
                          style={{
                            marginTop: '10px',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '4px',
                            padding: '4px 8px',
                            borderRadius: '5px',
                            border: '1px solid rgba(239,68,68,0.35)',
                            background: 'rgba(239,68,68,0.08)',
                            color: 'rgba(239,68,68,0.75)',
                            fontSize: '10px',
                            fontWeight: 600,
                            cursor: 'pointer',
                            width: '100%',
                            justifyContent: 'center',
                            transition: 'all 0.15s',
                          }}
                          onMouseEnter={e => {
                            (e.currentTarget as HTMLButtonElement).style.background = 'rgba(239,68,68,0.16)'
                            ;(e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(239,68,68,0.6)'
                          }}
                          onMouseLeave={e => {
                            (e.currentTarget as HTMLButtonElement).style.background = 'rgba(239,68,68,0.08)'
                            ;(e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(239,68,68,0.35)'
                          }}
                        >
                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/>
                            <line x1="4" y1="22" x2="4" y2="15"/>
                          </svg>
                          Report Issue
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* ─── RIGHT: Insights Sidebar ─── */}
              <div ref={insightsReveal.ref} className={`lg:w-[300px] shrink-0 flex flex-col gap-4 ${insightsReveal.className}`}>

                {/* Upgrade prompt for Free users — blurred-preview style */}
                {!hasFullDashboard && (
                  <div className="relative border border-border rounded-2xl overflow-hidden">
                    {/* Blurred fake preview behind the lock */}
                    <div className="absolute inset-0 pointer-events-none select-none" aria-hidden="true" style={{ filter: 'blur(8px)', opacity: 0.55 }}>
                      <div className="p-5">
                        <div className="h-2 w-24 bg-foreground/15 rounded mb-3" />
                        <div className="flex items-end gap-1 h-16 mb-4">
                          {[42, 58, 51, 67, 72, 64, 78].map((h, i) => (
                            <div key={i} className={`flex-1 rounded-t-md ${h >= 70 ? 'bg-green-500/60' : h >= 50 ? 'bg-amber-400/60' : 'bg-red-400/60'}`} style={{ height: `${h}%` }} />
                          ))}
                        </div>
                        <div className="space-y-2">
                          {[68, 54, 81, 47, 72].map((w, i) => (
                            <div key={i} className="flex items-center gap-2">
                              <div className="h-2 w-16 bg-foreground/15 rounded" />
                              <div className="flex-1 h-2 bg-foreground/[0.06] rounded-full overflow-hidden">
                                <div className={`h-full rounded-full ${w >= 70 ? 'bg-green-500/70' : w >= 50 ? 'bg-amber-400/70' : 'bg-red-400/70'}`} style={{ width: `${w}%` }} />
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                    {/* Cream wash so it reads */}
                    <div className="absolute inset-0 pointer-events-none" style={{ background: 'linear-gradient(180deg, hsl(46 100% 97% / 0.35) 0%, hsl(46 100% 97% / 0.85) 60%, hsl(46 100% 97%) 100%)' }} />

                    <div className="relative p-5 pt-7 text-center">
                      <div className="w-11 h-11 rounded-full bg-foreground/[0.06] border border-border flex items-center justify-center mx-auto mb-3 shadow-sm">
                        <svg className="w-5 h-5 text-foreground/40" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z" />
                        </svg>
                      </div>
                      <p className="text-[14px] font-display font-bold text-foreground/75 mb-1">Unlock full insights</p>
                      <p className="text-[11px] text-foreground/35 font-body mb-4 leading-relaxed">
                        See what's holding your videos back
                      </p>
                      <ul className="text-left text-[11px] text-foreground/50 font-body space-y-1.5 mb-4 max-w-[220px] mx-auto">
                        {[
                          'Track your score over time',
                          'Strengths & weaknesses by parameter',
                          'Personalised Prysm coach',
                        ].map(item => (
                          <li key={item} className="flex items-start gap-2">
                            <svg className="w-3 h-3 mt-0.5 shrink-0 text-green-600/70" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                            </svg>
                            <span>{item}</span>
                          </li>
                        ))}
                      </ul>
                      <a href="/pricing" className="inline-block w-full py-2.5 rounded-xl bg-foreground text-background text-[12px] font-body font-bold hover:opacity-90 transition-opacity">
                        Upgrade to {nextPlan?.name} — {nextPlan?.price}
                      </a>
                    </div>
                  </div>
                )}

                {/* Score Over Time */}
                {ownScores.length >= 2 && hasFullDashboard && (() => {
                  const chronological = [...ownAnalyses].reverse() // oldest -> newest, parallel to ownScores
                  // Cap at 20 most recent so bars never overflow the box
                  const MAX_BARS = 20
                  const visibleScores = ownScores.length > MAX_BARS ? ownScores.slice(ownScores.length - MAX_BARS) : ownScores
                  const visibleAnalyses = chronological.length > MAX_BARS ? chronological.slice(chronological.length - MAX_BARS) : chronological
                  const chartH = 88
                  return (
                  <div className="border border-border rounded-2xl p-5">
                    <div className="flex items-center justify-between mb-4">
                      <p className="text-[9px] font-semibold tracking-widest uppercase text-foreground/20">Your Score Over Time</p>
                      {ownScores.length > MAX_BARS && (
                        <span className="text-[8px] font-body text-foreground/20">Last {MAX_BARS} of {ownScores.length}</span>
                      )}
                    </div>
                    <div className="relative" style={{ height: chartH + 12, overflow: 'visible' }}>
                      {/* Gridlines at 60 (good) and 75 (great) */}
                      <div className="absolute left-0 right-0 border-t border-dashed border-green-500/15"
                        style={{ top: chartH - (75 / 100) * chartH }}>
                        <span className="absolute -top-[7px] right-0 text-[7px] text-green-600/40 font-body bg-background px-1">75</span>
                      </div>
                      <div className="absolute left-0 right-0 border-t border-dashed border-amber-500/15"
                        style={{ top: chartH - (60 / 100) * chartH }}>
                        <span className="absolute -top-[7px] right-0 text-[7px] text-amber-600/40 font-body bg-background px-1">60</span>
                      </div>
                      <div className="flex items-end gap-0.5 absolute inset-x-0 bottom-0" style={{ height: chartH, overflow: 'visible' }}>
                        {visibleScores.map((s, i) => {
                          const a = visibleAnalyses[i]
                          const showLabel = visibleScores.length <= 12
                          return (
                          <div key={i} className="flex-1 min-w-0 flex flex-col items-center justify-end h-full group/bar relative" style={{ overflow: 'visible' }}>
                            {showLabel && <span className="text-[8px] text-foreground/30 font-body mb-1 font-semibold leading-none">{s}</span>}
                            <div
                              className={`w-full rounded-t-sm transition-all duration-300 group-hover/bar:brightness-110 cursor-pointer ${s >= 70 ? 'bg-green-500/65' : s >= 50 ? 'bg-amber-400/70' : 'bg-red-400/65'}`}
                              style={{ height: `${Math.max((s / 100) * (chartH - (showLabel ? 12 : 4)), 4)}px`, opacity: 0, animation: `fade-slide-in 0.4s ease-out ${i * 40}ms forwards` }}
                              onClick={() => a && viewAnalysis(a)}
                            />
                            {/* Tooltip on hover */}
                            {a && (
                              <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 opacity-0 group-hover/bar:opacity-100 transition-opacity duration-200 pointer-events-none z-50 whitespace-nowrap">
                                <div className="bg-foreground text-background rounded-lg px-2.5 py-1.5 shadow-lg">
                                  <p className="text-[9px] font-display font-bold leading-tight">Score {s}</p>
                                  <p className="text-[8px] font-body opacity-70 max-w-[140px] truncate">{a.brief || a.summary}</p>
                                  <p className="text-[7px] font-body opacity-50">{formatDate(a.created_at)}</p>
                                </div>
                                <div className="absolute top-full left-1/2 -translate-x-1/2 -mt-1 w-2 h-2 rotate-45 bg-foreground" />
                              </div>
                            )}
                          </div>
                          )
                        })}
                      </div>
                    </div>
                    <div className="flex items-center justify-between mt-2 pt-2 border-t border-border/50">
                      <span className="text-[8px] text-foreground/15 font-body">{ownScores.length > MAX_BARS ? `−${ownScores.length - MAX_BARS} older` : 'First'}</span>
                      {visibleScores[visibleScores.length - 1] >= visibleScores[0] ? (
                        <span className="text-[9px] text-green-600/60 font-body font-medium">↑ {visibleScores[visibleScores.length - 1] - visibleScores[0]} points</span>
                      ) : (
                        <span className="text-[9px] text-red-500/50 font-body font-medium">↓ {visibleScores[0] - visibleScores[visibleScores.length - 1]} points</span>
                      )}
                      <span className="text-[8px] text-foreground/15 font-body">Latest</span>
                    </div>
                  </div>
                  )
                })()}

                {/* Parameter Heatmap — top 3 strengths & top 3 weaknesses */}
                {sortedParams.length > 0 && hasFullDashboard && (() => {
                  const sortedDesc = [...sortedParams].reverse() // highest first
                  const topStrengths = sortedDesc.slice(0, 3)
                  const topWeaknesses = sortedParams.slice(0, 3) // lowest first
                  const hasMore = sortedParams.length > 6
                  const renderRow = ([name, avg]: [string, number], i: number, prefix: string) => (
                    <div key={`${prefix}-${name}`} className="flex items-center gap-2"
                      style={{ opacity: 0, animation: `fade-slide-in 0.3s ease-out ${i * 50}ms forwards` }}>
                      <span className="w-[90px] shrink-0 text-[10px] font-body text-foreground/35 truncate">{name}</span>
                      <div className="flex-1 h-2.5 bg-foreground/[0.03] rounded-full overflow-hidden">
                        <div className={`h-full rounded-full ${heatColor(avg)}`}
                          style={{ width: `${avg}%`, transition: `width 0.6s ease-out ${i * 50}ms` }} />
                      </div>
                      <span className={`w-5 text-right text-[10px] font-bold font-display ${scoreColor(avg)}`}>{avg}</span>
                    </div>
                  )
                  return (
                  <div className="border border-border rounded-2xl p-5">
                    <p className="text-[9px] font-semibold tracking-widest uppercase text-foreground/20 mb-1">Strengths & Weaknesses</p>
                    <p className="text-[9px] text-foreground/15 font-body mb-4">Average across your {ownAnalyses.length} video{ownAnalyses.length !== 1 ? 's' : ''}</p>

                    {paramsExpanded ? (
                      <div className="flex flex-col gap-2">
                        {sortedParams.map(([n, a], i) => renderRow([n, a], i, 'all'))}
                      </div>
                    ) : (
                      <div className="flex flex-col gap-3.5">
                        {topStrengths.length > 0 && (
                          <div>
                            <div className="flex items-center gap-1.5 mb-2">
                              <span className="w-1 h-1 rounded-full bg-green-500/60" />
                              <p className="text-[8px] font-body font-semibold tracking-wider uppercase text-green-700/45">Strengths</p>
                            </div>
                            <div className="flex flex-col gap-2">
                              {topStrengths.map((p, i) => renderRow(p, i, 's'))}
                            </div>
                          </div>
                        )}
                        {topWeaknesses.length > 0 && (
                          <div>
                            <div className="flex items-center gap-1.5 mb-2">
                              <span className="w-1 h-1 rounded-full bg-red-400/60" />
                              <p className="text-[8px] font-body font-semibold tracking-wider uppercase text-red-600/45">Needs work</p>
                            </div>
                            <div className="flex flex-col gap-2">
                              {topWeaknesses.map((p, i) => renderRow(p, i, 'w'))}
                            </div>
                          </div>
                        )}
                      </div>
                    )}

                    {hasMore && (
                      <button onClick={() => setParamsExpanded(v => !v)}
                        className="mt-4 w-full text-[10px] text-foreground/35 hover:text-foreground/60 font-body font-semibold transition-colors cursor-pointer flex items-center justify-center gap-1 py-1">
                        {paramsExpanded ? 'Show less' : `View all ${sortedParams.length} parameters`}
                        <span className={`transition-transform duration-300 ${paramsExpanded ? 'rotate-180' : ''}`}>↓</span>
                      </button>
                    )}
                  </div>
                  )
                })()}

                {/* Recurring Issues — coach style */}
                {recurringIssues.length > 0 && hasFullDashboard && (
                  <div className="border border-border rounded-2xl p-5">
                    <p className="text-[9px] font-semibold tracking-widest uppercase text-foreground/20 mb-4">Keep working on</p>
                    <div className="flex flex-col gap-3">
                      {recurringIssues.map((issue, i) => (
                        <div key={i} className="flex items-start gap-3"
                          style={{ opacity: 0, animation: `fade-slide-in 0.3s ease-out ${i * 80}ms forwards` }}>
                          <div className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold text-white shrink-0 ${
                            issue.avgScore < 40 ? 'bg-red-400' : 'bg-amber-400'
                          }`}>
                            {i + 1}
                          </div>
                          <div>
                            <p className="text-[11px] font-display font-semibold text-foreground/55">{issue.param}</p>
                            <p className="text-[9px] text-foreground/25 font-body leading-relaxed mt-0.5">
                              avg {issue.avgScore}/100 across {issue.count} video{issue.count !== 1 ? 's' : ''}
                            </p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

              </div>
            </div>

          </>
        )}
      </div>

      {/* ═══ FEEDBACK MODAL ═══ */}
      {feedbackModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-[100] flex items-center justify-center p-6"
          onClick={() => setFeedbackModal(null)}>
          <div onClick={e => e.stopPropagation()}
            className="bg-background border border-border rounded-2xl p-7 max-w-md w-full shadow-2xl"
            style={{ animation: 'fade-slide-in 0.3s ease-out' }}>
            <h2 className="font-display text-lg font-bold mb-1">How did it perform?</h2>
            <p className="text-[13px] text-foreground/35 font-body mb-5">
              Share your actual results — this helps Prysm give better predictions.
            </p>

            <div className="grid grid-cols-2 gap-3 mb-4">
              {[
                { key: 'views', label: 'Views' },
                { key: 'likes', label: 'Likes' },
                { key: 'comments', label: 'Comments' },
                { key: 'saves', label: 'Saves' },
                { key: 'shares', label: 'Shares' },
              ].map(f => (
                <div key={f.key}>
                  <label className="text-[9px] font-semibold tracking-widest uppercase text-foreground/25 mb-1 block">{f.label}</label>
                  <input type="number" value={(feedbackForm as any)[f.key]}
                    onChange={e => setFeedbackForm(prev => ({ ...prev, [f.key]: e.target.value }))}
                    placeholder="0"
                    className="w-full bg-secondary/50 border border-border rounded-lg px-3 py-2 text-sm text-foreground placeholder:text-foreground/15 outline-none focus:border-foreground/25 transition-colors font-body" />
                </div>
              ))}
            </div>

            <label className="flex items-center gap-2 mb-5 cursor-pointer">
              <input type="checkbox" checked={feedbackForm.implemented}
                onChange={e => setFeedbackForm(prev => ({ ...prev, implemented: e.target.checked }))}
                className="w-4 h-4 rounded border-border" />
              <span className="text-[13px] text-foreground/40 font-body">I applied Prysm's suggestions</span>
            </label>

            <div className="flex gap-2">
              <button onClick={() => setFeedbackModal(null)}
                className="flex-1 py-2.5 rounded-xl border border-border text-foreground/35 text-sm font-medium cursor-pointer hover:bg-secondary/50 transition-colors">Cancel</button>
              <button onClick={() => submitFeedback(feedbackModal)} disabled={feedbackSubmitting}
                className="flex-1 py-2.5 rounded-xl bg-foreground text-background text-sm font-semibold cursor-pointer hover:opacity-90 transition-opacity disabled:opacity-50">
                {feedbackSubmitting ? 'Submitting...' : 'Submit →'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
