import { useEffect, useState, useCallback } from 'react'
import { parseMarkedSteps } from '@/studio/steps'
import { useParams } from 'react-router-dom'
import {
  Zap, Eye, Music, Scissors, Type, MousePointerClick,
  TrendingUp, Image, MessageSquare, Bookmark, RotateCcw, Share2,
  ChevronDown, Lightbulb,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { apiUrl } from '@/lib/api'

interface Parameter {
  name: string
  weight: number
  score: number
  advisory?: boolean
  problem: string
  fix: string
  whatIsHappening: string
  whyItMatters: string
  whatToChange: string
  howToDoIt: string
  whatToExpect: string
  referenceFrame?: number
  frameAnnotation?: string
}

interface EnergyCurvePoint { t: number; energy: number }
interface LowEnergySegment { start: number; end: number; avg_energy: number }
interface EnergyCurve {
  curve: EnergyCurvePoint[]
  low_energy_segments: LowEnergySegment[]
  hook_energy: number
  mid_energy: number
  closing_energy: number
  drop_detected: boolean
  drop_at_seconds: number | null
  duration: number
  avg_energy: number
}

interface Result {
  overallScore: number
  scoreLabel: string
  summary: string
  biggestProblem: string
  quickFix: string
  predictedScoreAfterFixes: number
  topPriorities: string[]
  suggestedHashtags: string[]
  suggestedCaption: string
  trendingAudioSuggestion: string
  parameters: Parameter[]
  niche: string
  platform: string
  intent?: 'my_video' | 'competitor'
  source_type?: string
  postMetadata?: any
  energyCurve?: EnergyCurve
  nextVideoTemplate?: {
    hook?: string
    structure?: string
    audio?: string
    location?: string
    text?: string
    cta?: string
    caption?: string
  }
  engagementPrediction?: {
    nawp: number
    ecr: number
    hook_risk: 'low' | 'medium' | 'high'
    retention_risk: 'low' | 'medium' | 'high'
    source: 'runpod' | 'heuristic'
    interpretation: string
  }
}

/* ── Helpers ───────────────────────────────────────────────────────────────── */

function severityBadge(score: number) {
  if (score < 40) return { label: 'CRITICAL', bg: 'bg-red-100 text-red-700 border-red-200' }
  if (score < 55) return { label: 'NEEDS WORK', bg: 'bg-amber-100 text-amber-700 border-amber-200' }
  if (score < 70) return { label: 'IMPROVE', bg: 'bg-amber-50 text-amber-600 border-amber-100' }
  if (score < 85) return { label: 'GOOD', bg: 'bg-green-50 text-green-600 border-green-100' }
  return { label: 'VIRAL-READY', bg: 'bg-green-100 text-green-700 border-green-200' }
}

function scoreBarColor(score: number) {
  if (score >= 70) return 'bg-green-500/80'
  if (score >= 50) return 'bg-amber-500/70'
  return 'bg-red-500/60'
}

function scoreTextColor(score: number) {
  if (score >= 70) return 'text-green-600'
  if (score >= 50) return 'text-amber-600'
  return 'text-red-500'
}

function severityGradient(score: number) {
  if (score < 40) return 'from-red-500/10 to-red-500/[0.02]'
  if (score < 55) return 'from-amber-500/10 to-amber-500/[0.02]'
  if (score < 70) return 'from-amber-400/8 to-amber-400/[0.01]'
  return 'from-green-500/8 to-green-500/[0.01]'
}

function severityDotColor(score: number) {
  if (score < 40) return 'bg-red-500'
  if (score < 55) return 'bg-amber-500'
  if (score < 70) return 'bg-amber-400'
  return 'bg-green-500'
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K'
  return String(n)
}

function parseSteps(text: string): string[] {
  if (!text) return []

  // Hackathon: "Step N —" markers (the backend's canonical format): split only at the markers
  const marked = parseMarkedSteps(text)
  if (marked) return marked

  const numberedParts = text.split(/(?:^|\s)\d+[\.\)]\s+/).map(s => s.trim()).filter(Boolean)
  if (numberedParts.length >= 2) {
    return numberedParts
      .map(s => s.replace(/\.\s*$/, '').trim())
      .filter(s => s.length > 8)
  }

  const lines = text.split(/\n+/).map(s => s.trim()).filter(Boolean)
  if (lines.length >= 2) {
    const allTerminated = lines.every(l => /[.!?]$/.test(l) || /^[-•→*\d]/.test(l))
    if (allTerminated) {
      const cleaned = lines
        .map(s => s.replace(/^[-•→*]\s*/, '').replace(/^\d+[\.\):\-]\s*/, '').trim())
        .map(s => s.replace(/\.$/, ''))
        .filter(s => s.length > 8)
      if (cleaned.length >= 2) return cleaned
    }
  }

  const byArrow = text.split(/\s*→\s*/).filter(s => s.trim().length > 5)
  if (byArrow.length >= 3) return byArrow.map(s => s.replace(/\.$/, '').trim())

  const normalised = text.replace(/\s*\n+\s*/g, ' ').trim()
  const sentences = normalised.split(/(?<=[.!?])\s+(?=[A-Z])/)
    .map(s => s.trim())
    .filter(s => s.length > 10)
    .map(s => s.replace(/[.!?]$/, ''))
  if (sentences.length >= 2 && sentences.length <= 8) return sentences

  return [normalised || text]
}

function getParamIcon(name: string): LucideIcon {
  const n = name.toLowerCase()
  if (n.includes('hook') || n.includes('opening')) return Zap
  if (n.includes('retention') || n.includes('pacing')) return Scissors
  if (n.includes('shareability') || n.includes('share')) return Share2
  if (n.includes('audio') || n.includes('music')) return Music
  if (n.includes('on-screen') || n.includes('text')) return Type
  if (n.includes('call to action') || n.includes('cta')) return MousePointerClick
  if (n.includes('trend')) return TrendingUp
  if (n.includes('visual') || n.includes('originality')) return Image
  if (n.includes('save')) return Bookmark
  if (n.includes('replay')) return RotateCcw
  if (n.includes('caption') || n.includes('hashtag')) return MessageSquare
  if (n.includes('thumbnail') || n.includes('frame')) return Eye
  return Eye
}

/* ── Audio Energy Curve ────────────────────────────────────────────────────── */

function AudioEnergyCurve({ energy }: { energy: EnergyCurve }) {
  if (!energy?.curve?.length) return null
  const pts = energy.curve
  const duration = energy.duration || pts[pts.length - 1]?.t || 1
  const low = energy.low_energy_segments || []

  const W = 300, H = 56, pad = 4
  const innerW = W - pad * 2, innerH = H - pad * 2
  const toX = (t: number) => pad + (t / duration) * innerW
  const toY = (e: number) => pad + innerH - e * innerH
  const pathD = pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${toX(p.t).toFixed(1)} ${toY(p.energy).toFixed(1)}`).join(' ')
  const fillD = pathD + ` L ${toX(pts[pts.length - 1].t).toFixed(1)} ${(pad + innerH).toFixed(1)} L ${toX(pts[0].t).toFixed(1)} ${(pad + innerH).toFixed(1)} Z`

  return (
    <div className="border border-border rounded-xl p-3.5 bg-background mb-5">
      <div className="flex items-center justify-between mb-2.5">
        <p className="text-[10px] font-semibold tracking-widest uppercase text-foreground/30">Audio Energy</p>
        {energy.drop_detected && (
          <span className="text-[9px] font-bold tracking-wider uppercase px-1.5 py-0.5 rounded border border-red-200/50 bg-red-50/40 text-red-600/70">
            Drop at {energy.drop_at_seconds}s
          </span>
        )}
      </div>
      <div className="relative w-full overflow-hidden rounded-lg" style={{ height: H }}>
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-full" preserveAspectRatio="none">
          {low.map((seg, i) => (
            <rect key={i} x={toX(seg.start)} y={pad} width={toX(seg.end) - toX(seg.start)} height={innerH} fill="rgba(239,68,68,0.08)" />
          ))}
          <path d={fillD} fill="rgba(34,197,94,0.08)" />
          <path d={pathD} fill="none" stroke="rgba(34,197,94,0.6)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          {low.map((seg, i) => (
            <line key={i} x1={toX(seg.start)} y1={pad} x2={toX(seg.start)} y2={pad + innerH} stroke="rgba(239,68,68,0.35)" strokeWidth="1" strokeDasharray="2,2" />
          ))}
        </svg>
      </div>
      <div className="flex items-center justify-between mt-2">
        {[
          { label: 'Hook', val: energy.hook_energy, thresh: 0.6 },
          { label: 'Mid', val: energy.mid_energy, thresh: 0.5 },
          { label: 'Closing', val: energy.closing_energy, thresh: 0.5 },
          { label: 'Avg', val: energy.avg_energy, thresh: -1 },
        ].map(({ label, val, thresh }) => (
          <div key={label} className="text-center">
            <p className="text-[9px] text-foreground/25 font-body">{label}</p>
            <p className={`text-[11px] font-bold font-display ${thresh < 0 ? 'text-foreground/40' : val >= thresh ? 'text-green-600/70' : 'text-amber-600/70'}`}>
              {Math.round(val * 100)}%
            </p>
          </div>
        ))}
      </div>
      {low.length > 0 && (
        <div className="mt-2.5 flex flex-col gap-1">
          {low.slice(0, 2).map((seg, i) => (
            <p key={i} className="text-[10px] text-red-600/60 font-body">↓ Low energy {seg.start}s–{seg.end}s — viewer drop-off risk</p>
          ))}
        </div>
      )}
    </div>
  )
}

/* ══════════════════════════════════════════════════════════════════════════ */
/*  SHARED RESULTS COMPONENT                                                 */
/* ══════════════════════════════════════════════════════════════════════════ */

export default function SharedResults() {
  const { shareId } = useParams<{ shareId: string }>()
  const [result, setResult] = useState<Result | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedFix, setExpandedFix] = useState<number | null>(null)
  const [expandedWhy, setExpandedWhy] = useState<Set<number>>(new Set())
  const [expandedBlueprint, setExpandedBlueprint] = useState<Set<number>>(new Set())
  const [copied, setCopied] = useState<string | null>(null)
  const [scoreAnimated, setScoreAnimated] = useState(0)

  useEffect(() => {
    if (!shareId) return
    setLoading(true)
    fetch(apiUrl(`/prysm/shared/${shareId}`))
      .then(res => {
        if (!res.ok) throw new Error('Analysis not found')
        return res.json()
      })
      .then(data => {
        if (!data.topPriorities) data.topPriorities = []
        if (!data.parameters) data.parameters = []
        if (!data.scoreLabel) data.scoreLabel = 'Analysed'
        if (!data.summary) data.summary = 'Analysis complete.'
        if (!data.biggestProblem) data.biggestProblem = data.topPriorities?.[0] || ''
        if (!data.quickFix) data.quickFix = ''
        if (!data.suggestedHashtags) data.suggestedHashtags = []
        if (!data.suggestedCaption) data.suggestedCaption = ''
        if (!data.predictedScoreAfterFixes) data.predictedScoreAfterFixes = Math.min(data.overallScore + 15, 98)
        setResult(data)
        setError(null)

        // Spring score animation
        let current = 0
        const target = data.overallScore
        const overshoot = Math.min(target + 8, 100)
        let phase: 'up' | 'over' | 'settle' = 'up'
        const interval = setInterval(() => {
          if (phase === 'up') {
            current += 3
            if (current >= overshoot) { current = overshoot; phase = 'over' }
          } else if (phase === 'over') {
            current -= 1
            if (current <= target) { current = target; phase = 'settle'; clearInterval(interval) }
          }
          setScoreAnimated(current)
        }, 16)
        return () => clearInterval(interval)
      })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [shareId])

  const copyText = useCallback((text: string, label: string) => {
    navigator.clipboard.writeText(text)
    setCopied(label)
    setTimeout(() => setCopied(null), 2000)
  }, [])

  function toggleFix(i: number) {
    setExpandedFix(prev => prev === i ? null : i)
  }
  function toggleWhy(i: number) {
    setExpandedWhy(prev => { const n = new Set(prev); n.has(i) ? n.delete(i) : n.add(i); return n })
  }

  // Loading state
  if (loading) return (
    <div className="min-h-screen bg-background flex items-center justify-center">
      <div className="text-center">
        <div className="w-10 h-10 border-2 border-foreground/10 border-t-foreground/40 rounded-full animate-spin mx-auto mb-4" />
        <p className="text-foreground/30 font-body text-sm">Loading shared analysis...</p>
      </div>
    </div>
  )

  // Error / not found
  if (error || !result) return (
    <div className="min-h-screen bg-background flex items-center justify-center">
      <div className="text-center max-w-sm px-6">
        <div className="w-16 h-16 rounded-2xl bg-foreground/[0.04] border border-border flex items-center justify-center mx-auto mb-5">
          <Eye className="w-7 h-7 text-foreground/20" />
        </div>
        <h1 className="font-display text-xl font-bold mb-2">Analysis not found</h1>
        <p className="text-foreground/40 font-body text-sm mb-6">This link may have expired or been revoked.</p>
        <a href="/analyse" className="inline-block px-6 py-2.5 rounded-xl bg-foreground text-background text-sm font-display font-semibold hover:opacity-90 transition-opacity">
          Analyse your own video
        </a>
      </div>
    </div>
  )

  const intent = result.intent || 'my_video'
  const sourceType = result.source_type || 'upload'
  const isCompetitor = intent === 'competitor'
  const isUpload = sourceType === 'upload'

  // Plan-aware display — use sharer's plan so paid sharers show full results
  const sharerPlan: string = (result as any).sharer_plan || 'free'
  const planRank: Record<string, number> = { free: 0, pro: 1, plus: 2, creator: 3 }
  const sharerPlanRank = planRank[sharerPlan] ?? 0
  const isPaidSharer = sharerPlanRank >= 1

  const isCaptionHashtagParam = (p: Parameter) => p.name.toLowerCase().includes('caption') || p.name.toLowerCase().includes('hashtag')
  const scoredParams = isUpload ? result.parameters.filter((p: Parameter) => !isCaptionHashtagParam(p)) : result.parameters

  // Advisory / main split — name-based fallback for old cached results
  const ADVISORY_NAMES = new Set(['Call to Action', 'On-Screen Text', 'Save Potential', 'Replay Value'])
  const isAdvisory = (p: Parameter) => p.advisory === true || ADVISORY_NAMES.has(p.name)
  const mainParams = scoredParams.filter(p => !isAdvisory(p))
  const advisoryParams = scoredParams.filter(p => isAdvisory(p))

  // Gating: paid sharer = show everything; free sharer = show 2 lowest params only
  const FREE_VISIBLE_COUNT = 2
  const barParams = isPaidSharer
    ? (isCompetitor ? [...mainParams].sort((a, b) => b.score - a.score) : [...mainParams].sort((a, b) => a.score - b.score))
    : [...mainParams].sort((a, b) => a.score - b.score).slice(0, FREE_VISIBLE_COUNT)
  const freeBarParams = barParams  // used in parameter bars section
  const lockedCount = isPaidSharer ? 0 : (mainParams.length - FREE_VISIBLE_COUNT + advisoryParams.length)

  const fixParams = isPaidSharer
    ? [...mainParams].sort((a, b) => a.score - b.score)
    : [...mainParams].sort((a, b) => a.score - b.score).slice(0, FREE_VISIBLE_COUNT)
  const freeFixParams = fixParams

  const weakParams = freeFixParams.filter(p => p.score < 85)
  const strongParams = [...mainParams].filter(p => p.score >= 85).sort((a, b) => b.score - a.score)
  const weakAdvisoryParams = [...advisoryParams].sort((a, b) => a.score - b.score)

  const mainParamsSorted = isCompetitor
    ? [...mainParams].sort((a, b) => b.score - a.score)
    : [...mainParams].sort((a, b) => a.score - b.score)
  const advisoryParamsSorted = [...advisoryParams].sort((a, b) => a.score - b.score)

  const hasTrendingSection = (!isUpload && (result.suggestedHashtags?.length > 0 || result.suggestedCaption || result.trendingAudioSuggestion))
  const hasUploadCaptionSection = isUpload && (result.suggestedHashtags?.length > 0 || result.suggestedCaption)
  const hasPostData = result.postMetadata && (result.postMetadata.likeCount > 0 || result.postMetadata.viewCount > 0)
  const paramCount = result.parameters.length || scoredParams.length

  const circumference = 2 * Math.PI * 54
  const offset = circumference - (scoreAnimated / 100) * circumference
  const ringColor = result.overallScore >= 70 ? 'stroke-green-500' : result.overallScore >= 50 ? 'stroke-amber-500' : 'stroke-red-500'

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* ═══ NAV ═══ */}
      <nav className="border-b border-border px-3 sm:px-8 h-14 flex items-center justify-between sticky top-0 bg-background/90 backdrop-blur-md z-50">
        <a href="/" className="font-logo text-[18px] sm:text-[21px] text-foreground">PRYSM</a>
        <div className="flex items-center gap-2 sm:gap-4">
          <a href="/analyse" className="text-xs sm:text-sm text-foreground/40 hover:text-foreground/60 transition-colors">
            Analyse your own video →
          </a>
        </div>
      </nav>

      {/* ═══ SHARED BANNER ═══ */}
      <div className="bg-foreground/[0.03] border-b border-border px-4 py-2.5 text-center">
        <p className="text-[12px] text-foreground/40 font-body">
          Shared analysis — <a href="/register" className="underline hover:text-foreground/60 transition-colors">Sign up</a> to analyse your own videos
        </p>
      </div>

      {/* ═══ CONTENT ═══ */}
      <div className="max-w-3xl mx-auto px-3 sm:px-6 py-4 sm:py-8">

        {/* ═══ POST PERFORMANCE (for link analyses) ═══ */}
        {hasPostData && (
          <div className="border border-border rounded-xl p-4 bg-background mb-5"
            style={{ opacity: 0, animation: 'fade-slide-in 0.5s ease-out forwards' }}>
            <p className="text-[10px] font-semibold tracking-widest uppercase text-foreground/30 mb-3">Post Performance</p>
            <div className="flex items-baseline gap-6">
              {result.postMetadata.viewCount > 0 && (
                <div>
                  <p className="text-xl font-display font-black leading-none tracking-tight">{formatNumber(result.postMetadata.viewCount)}</p>
                  <p className="text-[11px] text-foreground/40 font-body mt-1">Views</p>
                </div>
              )}
              {result.postMetadata.likeCount > 0 && (
                <div>
                  <p className="text-xl font-display font-black leading-none tracking-tight">{formatNumber(result.postMetadata.likeCount)}</p>
                  <p className="text-[11px] text-foreground/40 font-body mt-1">Likes</p>
                </div>
              )}
              {result.postMetadata.commentCount > 0 && (
                <div>
                  <p className="text-xl font-display font-black leading-none tracking-tight">{formatNumber(result.postMetadata.commentCount)}</p>
                  <p className="text-[11px] text-foreground/40 font-body mt-1">Comments</p>
                </div>
              )}
            </div>
            {result.postMetadata.uploader && (
              <p className="text-[13px] text-foreground/50 font-body font-semibold mt-3">@{result.postMetadata.uploader}</p>
            )}
          </div>
        )}

        {/* ═══ SCORE CARD ═══ */}
        <div className="border border-border rounded-2xl p-4 sm:p-6 mb-5"
          style={{ opacity: 0, animation: 'fade-slide-in 0.6s ease-out forwards' }}>
          <div className="flex flex-col sm:flex-row items-center sm:items-center gap-3 sm:gap-6 mb-5">
            <div className="relative shrink-0" style={{ width: 90, height: 90 }}>
              <svg viewBox="0 0 120 120" className="w-full h-full -rotate-90">
                <circle cx="60" cy="60" r="54" fill="none" stroke="currentColor" strokeWidth="6" className="text-foreground/6" />
                <circle cx="60" cy="60" r="54" fill="none" strokeWidth="6" strokeLinecap="round"
                  className={`${ringColor} transition-all duration-300`}
                  strokeDasharray={circumference} strokeDashoffset={offset} />
              </svg>
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <span className="text-2xl sm:text-3xl font-display font-black tracking-tighter leading-none">{scoreAnimated}</span>
                <span className="text-[10px] sm:text-[11px] text-foreground/35 font-body mt-0.5">/100</span>
              </div>
            </div>

            <div className="flex-1 min-w-0 text-center sm:text-left">
              <span className={`inline-block text-[9px] sm:text-[10px] font-bold tracking-widest uppercase px-2.5 py-0.5 rounded-full border mb-2 ${severityBadge(result.overallScore).bg}`}>
                {result.scoreLabel}
              </span>
              {result.biggestProblem && (
                <p className="text-[13px] sm:text-[14px] text-foreground/55 font-body leading-relaxed mb-1.5">{result.biggestProblem}</p>
              )}
              {!isCompetitor && result.predictedScoreAfterFixes > result.overallScore && (
                <div className="flex items-center justify-center sm:justify-start gap-2">
                  <span className="text-[13px] sm:text-[14px] text-foreground/30 font-body">After fixes →</span>
                  <span className="font-display font-bold text-sm text-green-600/80">{result.predictedScoreAfterFixes}</span>
                  <span className="text-[13px] sm:text-[14px] text-green-600/50 font-body">(+{result.predictedScoreAfterFixes - result.overallScore})</span>
                </div>
              )}
            </div>
          </div>

          {result.quickFix && (
            <div className="bg-foreground/[0.03] border-l-[3px] border-green-500/60 rounded-r-xl p-4">
              <p className="text-[11px] font-semibold tracking-widest uppercase text-green-600/50 mb-1">{isCompetitor ? 'Apply this to your next video' : 'Quick win'}</p>
              <p className="text-[14px] text-foreground/65 font-body leading-relaxed">{result.quickFix}</p>
            </div>
          )}
        </div>

        {/* ═══ PARAMETER BARS ═══ */}
        <div className="border border-border rounded-2xl p-5 sm:p-6 mb-5"
          style={{ opacity: 0, animation: 'fade-slide-in 0.6s ease-out 100ms forwards' }}>
          <h2 className="font-display text-base font-bold tracking-tight mb-0.5">{paramCount}-Parameter {isCompetitor ? 'Analysis' : 'Breakdown'}</h2>
          <p className="text-foreground/30 text-[12px] mb-4 font-body">{isCompetitor ? 'How well this video executes each signal' : `Based on ${result.platform}'s actual ranking signals`}</p>

          <div className="flex flex-col gap-2.5">
            {/* Main parameter bars */}
            {freeBarParams.map((param, i) => {
              const badge = severityBadge(param.score)
              return (
                <div key={i} className="flex items-center gap-3"
                  style={{ opacity: 0, animation: `fade-slide-in 0.4s ease-out ${i * 50}ms forwards` }}>
                  <span className="w-[140px] sm:w-[180px] shrink-0 text-[13px] font-body text-foreground/50 leading-tight">{param.name}</span>
                  <div className="flex-1 h-2.5 bg-foreground/[0.04] rounded-full overflow-hidden">
                    <div className={`h-full rounded-full ${scoreBarColor(param.score)}`}
                      style={{ width: `${param.score}%`, transition: `width 0.8s ease-out ${i * 50 + 200}ms` }} />
                  </div>
                  <span className={`w-7 text-right text-[14px] font-bold font-display ${scoreTextColor(param.score)}`}>{param.score}</span>
                  <span className={`text-[9px] font-bold tracking-wider uppercase px-2 py-1 rounded-md border shrink-0 min-w-[70px] text-center ${badge.bg}`}>
                    {badge.label}
                  </span>
                </div>
              )
            })}

            {/* Locked parameters row — only for free sharer plan */}
            {lockedCount > 0 && (
              <div className="flex items-center gap-3 mt-1 pt-3 border-t border-border/50"
                style={{ opacity: 0, animation: 'fade-slide-in 0.4s ease-out 200ms forwards' }}>
                <div className="flex items-center gap-2 flex-1">
                  <svg className="w-3.5 h-3.5 text-foreground/25 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z" />
                  </svg>
                  <span className="text-[13px] font-body text-foreground/30">{lockedCount} more parameters locked</span>
                </div>
                <a href="/register" className="text-[12px] font-semibold text-foreground/50 hover:text-foreground transition-colors font-body shrink-0">
                  Unlock with Pro →
                </a>
              </div>
            )}

            {/* Advisory parameters — exactly matching Results.tsx style, paid sharers only */}
            {isPaidSharer && !isCompetitor && advisoryParamsSorted.length > 0 && (
              <>
                <div className="pt-2 mt-1 border-t border-border/50"
                  style={{ opacity: 0, animation: `fade-slide-in 0.4s ease-out ${mainParamsSorted.length * 50 + 100}ms forwards` }}>
                  <span className="text-[10px] font-bold tracking-wider uppercase text-foreground/25 font-body">Advisory — Does Not Affect Final Score</span>
                </div>
                {advisoryParamsSorted.map((param, i) => {
                  const badge = severityBadge(param.score)
                  return (
                    <div key={`adv-${i}`} className="flex items-center gap-3 opacity-70"
                      style={{ opacity: 0, animation: `fade-slide-in 0.4s ease-out ${(mainParamsSorted.length + i) * 50 + 150}ms forwards` }}>
                      <span className="w-[140px] sm:w-[180px] shrink-0 text-[13px] font-body text-foreground/50 leading-tight">{param.name}</span>
                      <div className="flex-1 h-2.5 bg-foreground/[0.04] rounded-full overflow-hidden">
                        <div className={`h-full rounded-full ${scoreBarColor(param.score)}`}
                          style={{ width: `${param.score}%`, transition: `width 0.8s ease-out ${(mainParamsSorted.length + i) * 50 + 350}ms` }} />
                      </div>
                      <span className={`w-7 text-right text-[14px] font-bold font-display ${scoreTextColor(param.score)}`}>{param.score}</span>
                      <span className={`text-[9px] font-bold tracking-wider uppercase px-2 py-1 rounded-md border shrink-0 min-w-[70px] text-center ${badge.bg}`}>
                        {badge.label}
                      </span>
                    </div>
                  )
                })}
              </>
            )}

            {/* Greyed-out Caption & Hashtag bar for uploads */}
            {isUpload && (
              <div className="flex items-center gap-3 mt-1 pt-3 border-t border-border/50"
                style={{ opacity: 0, animation: `fade-slide-in 0.4s ease-out ${(mainParamsSorted.length + advisoryParamsSorted.length) * 50}ms forwards` }}>
                <span className="w-[140px] sm:w-[180px] shrink-0 text-[13px] font-body text-foreground/25">Caption & Hashtags</span>
                <div className="flex-1 h-2.5 bg-foreground/[0.03] rounded-full overflow-hidden" />
                <span className="w-7 text-right text-[14px] font-bold font-display text-foreground/20">—</span>
                <span className="text-[10px] font-semibold tracking-wider uppercase px-2 py-1 rounded-md border border-border text-foreground/25 shrink-0 min-w-[70px] text-center">
                  See below ↓
                </span>
              </div>
            )}
          </div>
        </div>

        {/* ═══ AUDIO ENERGY CURVE — paid sharers only ═══ */}
        {isPaidSharer && result.energyCurve?.curve?.length ? (
          <div className="mb-5">
            <AudioEnergyCurve energy={result.energyCurve} />
          </div>
        ) : null}

        {/* ═══ ENGAGEMENT PREDICTION — paid sharers only ═══ */}
        {isPaidSharer && result.engagementPrediction && result.engagementPrediction.source === 'runpod' && (() => {
          const ep = result.engagementPrediction!
          const ecrRiskColor = ep.hook_risk === 'low' ? 'text-green-600' : ep.hook_risk === 'medium' ? 'text-amber-500' : 'text-red-500'
          const nawpRiskColor = ep.retention_risk === 'low' ? 'text-green-600' : ep.retention_risk === 'medium' ? 'text-amber-500' : 'text-red-500'
          const ecrBar = ep.hook_risk === 'low' ? 'bg-green-500' : ep.hook_risk === 'medium' ? 'bg-amber-400' : 'bg-red-500'
          const nawpBar = ep.retention_risk === 'low' ? 'bg-green-500' : ep.retention_risk === 'medium' ? 'bg-amber-400' : 'bg-red-500'
          return (
            <div className="border border-border rounded-2xl p-5 sm:p-6 mb-5">
              <div className="flex items-center justify-between mb-1">
                <h2 className="font-display text-base font-bold tracking-tight">Predicted Viewer Behaviour</h2>
                <span className="text-[9px] font-bold tracking-widest uppercase px-2 py-0.5 rounded-full border border-border text-foreground/30">AI Prediction</span>
              </div>
              <p className="text-foreground/30 text-[12px] mb-4 font-body">Prysm AI estimates from your video frames — not measured data, but a strong signal of how viewers will respond</p>
              <div className="flex flex-col gap-4">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-[13px] font-body text-foreground/60">Hook Hold Rate</span>
                    <div className="flex items-center gap-2">
                      <span className={`text-[15px] font-display font-black ${ecrRiskColor}`}>{Math.round(ep.ecr)}%</span>
                      <span className={`text-[9px] font-bold tracking-widest uppercase px-2 py-0.5 rounded-full border ${ep.hook_risk === 'low' ? 'bg-green-50 text-green-600 border-green-200' : ep.hook_risk === 'medium' ? 'bg-amber-50 text-amber-500 border-amber-200' : 'bg-red-50 text-red-500 border-red-200'}`}>{ep.hook_risk === 'low' ? 'strong hook' : ep.hook_risk === 'medium' ? 'average hook' : 'weak hook'}</span>
                    </div>
                  </div>
                  <div className="h-2 bg-foreground/[0.04] rounded-full overflow-hidden">
                    <div className={`h-full rounded-full ${ecrBar}`} style={{ width: `${ep.ecr}%` }} />
                  </div>
                  <p className="text-[11px] text-foreground/30 font-body mt-1">% of viewers predicted to watch past the first 5 seconds</p>
                </div>
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-[13px] font-body text-foreground/60">Average Watch Rate</span>
                    <div className="flex items-center gap-2">
                      <span className={`text-[15px] font-display font-black ${nawpRiskColor}`}>{Math.round(ep.nawp)}%</span>
                      <span className={`text-[9px] font-bold tracking-widest uppercase px-2 py-0.5 rounded-full border ${ep.retention_risk === 'low' ? 'bg-green-50 text-green-600 border-green-200' : ep.retention_risk === 'medium' ? 'bg-amber-50 text-amber-500 border-amber-200' : 'bg-red-50 text-red-500 border-red-200'}`}>{ep.retention_risk === 'low' ? 'high retention' : ep.retention_risk === 'medium' ? 'average retention' : 'low retention'}</span>
                    </div>
                  </div>
                  <div className="h-2 bg-foreground/[0.04] rounded-full overflow-hidden">
                    <div className={`h-full rounded-full ${nawpBar}`} style={{ width: `${ep.nawp}%` }} />
                  </div>
                  <p className="text-[11px] text-foreground/30 font-body mt-1">% of the video the average viewer is predicted to watch</p>
                </div>
              </div>
            </div>
          )
        })()}

        {/* ═══ FIX THIS FIRST / WHAT MADE THIS WORK ═══ */}
        {(isCompetitor ? freeFixParams.length > 0 : weakParams.length > 0) && (() => {
          const displayParams = isCompetitor ? freeFixParams : weakParams
          return (
          <div className="mb-5">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="font-display text-base font-bold tracking-tight">
                  {isCompetitor
                    ? `Techniques That ${result.postMetadata?.likeCount ? `Got ${formatNumber(result.postMetadata.likeCount)} Likes` : 'Made This Work'}`
                    : 'Fix This First'}
                </h2>
                {isCompetitor && (
                  <p className="text-[14px] text-foreground/30 font-body mt-0.5">Apply these to your content for similar results</p>
                )}
              </div>
              {!isCompetitor && (
                <span className="text-[14px] font-body font-semibold text-foreground/50 px-2.5 py-1 rounded-full border border-border bg-secondary/40">{weakParams.length} issues</span>
              )}
            </div>

            <div className="flex flex-col gap-3">
              {displayParams.map((param, i) => {
                const badge = severityBadge(param.score)
                const steps = parseSteps(param.howToDoIt || param.fix || param.whatToChange)
                const isFixExpanded = expandedFix === i
                const isWhyExpanded = expandedWhy.has(i)
                const ParamIcon = getParamIcon(param.name)

                return (
                  <div key={i}
                    className={`border rounded-2xl overflow-hidden transition-all duration-300 ${isCompetitor ? 'border-green-200/30' : ''}`}
                    style={{ opacity: 0, animation: `fade-slide-in 0.5s ease-out ${i * 80}ms forwards` }}>

                    {/* Header */}
                    <button
                      onClick={() => toggleFix(i)}
                      className={`w-full px-4 py-3.5 flex items-center gap-3 bg-gradient-to-r ${
                        isCompetitor
                          ? param.score >= 70 ? 'from-emerald-500/12 via-green-500/6 to-transparent' : param.score >= 50 ? 'from-amber-500/10 via-amber-400/4 to-transparent' : 'from-foreground/[0.04] to-transparent'
                          : severityGradient(param.score)
                      } border-b border-border/40 hover:brightness-[0.97] transition-all cursor-pointer`}
                    >
                      <div className={`w-8 h-8 sm:w-10 sm:h-10 rounded-lg sm:rounded-xl border flex items-center justify-center flex-shrink-0 ${
                        isCompetitor
                          ? param.score >= 70 ? 'bg-emerald-50 border-emerald-200/60' : param.score >= 50 ? 'bg-amber-50 border-amber-200/50' : 'bg-foreground/[0.03] border-foreground/10'
                          : param.score < 40 ? 'bg-red-50 border-red-200/50' : param.score < 55 ? 'bg-amber-50 border-amber-200/50' : param.score < 70 ? 'bg-amber-50/60 border-amber-200/40' : 'bg-green-50 border-green-200/50'
                      }`}>
                        <ParamIcon className={`w-4 h-4 sm:w-[18px] sm:h-[18px] ${
                          isCompetitor
                            ? param.score >= 70 ? 'text-emerald-600' : param.score >= 50 ? 'text-amber-600' : 'text-foreground/35'
                            : scoreTextColor(param.score)
                        } opacity-70`} />
                      </div>

                      <div className="flex-1 min-w-0 text-left">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-display font-bold text-[13px] sm:text-[15px] text-foreground">{param.name}</span>
                          {isCompetitor ? (
                            <span className={`text-[9px] font-bold tracking-wider uppercase px-2 py-1 rounded-md border shrink-0 ${
                              param.score >= 70 ? 'border-emerald-300/60 bg-emerald-50 text-emerald-700' :
                              param.score >= 50 ? 'border-amber-300/50 bg-amber-50 text-amber-700' :
                              'border-foreground/10 bg-foreground/[0.03] text-foreground/30'
                            }`}>
                              {param.score >= 70 ? 'REPLICATE' : param.score >= 50 ? 'ADAPT' : 'SKIP'}
                            </span>
                          ) : (
                            <span className={`text-[9px] font-bold tracking-wider uppercase px-1.5 py-0.5 rounded border shrink-0 ${badge.bg}`}>
                              {badge.label}
                            </span>
                          )}
                          {!isCompetitor && (
                            <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${severityDotColor(param.score)} ${param.score < 40 ? 'animate-pulse' : ''}`} />
                          )}
                        </div>
                      </div>

                      <span className={`font-display font-bold text-base shrink-0 ${
                        isCompetitor
                          ? param.score >= 70 ? 'text-emerald-600' : param.score >= 50 ? 'text-amber-600' : 'text-foreground/40'
                          : scoreTextColor(param.score)
                      }`}>
                        {param.score}
                      </span>

                      <ChevronDown className={`w-4 h-4 text-foreground/25 shrink-0 transition-transform duration-300 ${isFixExpanded ? 'rotate-180' : ''}`} />
                    </button>

                    {/* Expanded content */}
                    <div className={`grid transition-all duration-300 ease-in-out ${isFixExpanded ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}>
                      <div className="overflow-hidden">
                        <div className="px-5 pt-4 pb-5">
                          {/* Problem */}
                          {isCompetitor ? (
                            <div className="mb-4 border-l-[3px] border-emerald-400/50 pl-4 py-1">
                              <p className="text-[10px] font-bold tracking-widest uppercase text-emerald-700/50 mb-1.5">What they did</p>
                              <p className="text-[14px] font-body leading-[1.7] text-foreground/75">{param.problem || param.whatIsHappening}</p>
                            </div>
                          ) : (
                            <div className="mb-4">
                              <p className="text-[10px] font-bold tracking-widest uppercase text-foreground/35 mb-1.5">
                                {param.score >= 70 ? 'To reach viral-ready' : 'The problem'}
                              </p>
                              <p className={`text-[14px] font-body leading-[1.7] ${param.score < 40 ? 'text-red-700/65' : param.score >= 70 ? 'text-green-700/65' : 'text-amber-700/65'}`}>
                                {param.problem || param.whatIsHappening}
                              </p>
                            </div>
                          )}

                          <div className={`w-full h-px mb-4 ${isCompetitor ? 'bg-emerald-200/30' : 'bg-border/60'}`} />

                          {/* Fix */}
                          {isCompetitor ? (
                            <div className="mb-4 rounded-xl bg-emerald-50/40 border border-emerald-200/30 px-4 py-3.5">
                              <p className="text-[10px] font-bold tracking-widest uppercase text-emerald-700/50 mb-1.5">Apply to your content</p>
                              <p className="text-[14px] text-foreground/70 font-body leading-[1.7]">{param.fix || param.whatToChange}</p>
                            </div>
                          ) : (
                            <div className="mb-4">
                              <p className="text-[10px] font-bold tracking-widest uppercase text-foreground/35 mb-1.5">
                                {param.score >= 70 ? 'The refinement' : 'The fix'}
                              </p>
                              <p className="text-[14px] text-foreground/65 font-body leading-[1.7]">{param.fix || param.whatToChange}</p>
                            </div>
                          )}

                          {/* Steps */}
                          {steps.length >= 1 && (
                            <div className={`rounded-xl border p-4 mb-4 ${
                              isCompetitor ? 'border-emerald-200/40 bg-emerald-50/20' : 'border-border bg-secondary/40'
                            }`}>
                              <p className={`text-[10px] font-bold tracking-widest uppercase mb-3 ${
                                isCompetitor ? 'text-emerald-700/45' : 'text-foreground/40'
                              }`}>
                                {isCompetitor ? 'Key takeaways' : 'Step-by-step'}
                              </p>
                              <div className="flex flex-col gap-2.5">
                                {steps.map((step, j) => (
                                  <div key={j} className="flex gap-3 items-start"
                                    style={{ opacity: 0, animation: `fade-slide-in 0.3s ease-out ${j * 80}ms forwards` }}>
                                    <div className="w-5 h-5 rounded-full bg-foreground/[0.06] flex items-center justify-center shrink-0 mt-0.5">
                                      <span className="text-[10px] font-bold text-foreground/30">{j + 1}</span>
                                    </div>
                                    <p className="text-[13.5px] font-body leading-[1.7] text-foreground/65">{step}</p>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* Why this matters */}
                          {(param.whyItMatters || param.whatToExpect) && (
                            <div className={`rounded-xl mt-1 overflow-hidden ${
                              isCompetitor ? 'border border-amber-200/30' : 'border border-foreground/[0.08]'
                            }`}>
                              <button
                                onClick={(e) => { e.stopPropagation(); toggleWhy(i) }}
                                className={`w-full flex items-center gap-2.5 px-4 py-2.5 cursor-pointer transition-colors ${
                                  isCompetitor ? 'bg-amber-50/20 hover:bg-amber-50/40' : 'bg-foreground/[0.02] hover:bg-foreground/[0.04]'
                                }`}
                              >
                                <div className={`w-6 h-6 rounded-md border flex items-center justify-center flex-shrink-0 ${
                                  isCompetitor ? 'bg-amber-100/80 border-amber-300/50' : 'bg-amber-100 border-amber-200/60'
                                }`}>
                                  <Lightbulb className="w-3 h-3 text-amber-600" />
                                </div>
                                <span className={`text-[10px] font-bold tracking-widest uppercase flex-1 text-left ${
                                  isCompetitor ? 'text-amber-700/50' : 'text-foreground/40'
                                }`}>Why this matters</span>
                                <ChevronDown className={`w-3.5 h-3.5 text-foreground/25 transition-transform duration-200 ${isWhyExpanded ? 'rotate-180' : ''}`} />
                              </button>
                              <div className={`grid transition-all duration-200 ease-in-out ${isWhyExpanded ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}>
                                <div className="overflow-hidden">
                                  <div className="px-4 pb-4 pt-2">
                                    {param.whyItMatters && (
                                      <p className={`text-[13px] font-body leading-[1.7] ${isCompetitor ? 'text-foreground/60' : 'text-foreground/55'}`}>
                                        {param.whyItMatters}
                                      </p>
                                    )}
                                    {param.whatToExpect && (
                                      <div className="mt-3 flex items-start gap-2.5 rounded-lg bg-green-50/60 border border-green-200/50 px-3 py-2.5">
                                        <TrendingUp className="w-4 h-4 text-emerald-600 flex-shrink-0 mt-0.5" />
                                        <p className="text-[13px] font-body leading-[1.7] text-emerald-800/70">
                                          <span className="font-bold text-emerald-800/80">Expected: </span>{param.whatToExpect}
                                        </p>
                                      </div>
                                    )}
                                  </div>
                                </div>
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
          )
        })()}

        {/* ═══ PRO UPSELL LOCKED CARD — only for free sharer plan ═══ */}
        {!isPaidSharer && (
          <div className="border border-border/60 rounded-2xl p-5 mb-5 bg-foreground/[0.015]"
            style={{ opacity: 0, animation: 'fade-slide-in 0.5s ease-out 300ms forwards' }}>
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 rounded-lg bg-foreground/[0.04] border border-border flex items-center justify-center shrink-0 mt-0.5">
                <svg className="w-4 h-4 text-foreground/25" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z" />
                </svg>
              </div>
              <div className="flex-1">
                <p className="font-display font-bold text-[14px] text-foreground/60 mb-1">
                  {lockedCount} parameters hidden · Pro unlocks everything
                </p>
                <p className="text-[13px] text-foreground/35 font-body leading-relaxed">
                  Full 10-parameter breakdown, step-by-step fixes, audio energy curve, video blueprint, and ARIA AI chat — all in one analysis.
                </p>
                <a href="/register" className="inline-block mt-3 px-4 py-2 rounded-xl bg-foreground text-background text-[12px] font-display font-semibold hover:opacity-90 transition-opacity">
                  Get started free →
                </a>
              </div>
            </div>
          </div>
        )}

        {/* ═══ ADVISORY PARAMETERS — exactly matching Results.tsx ═══ */}
        {isPaidSharer && !isCompetitor && weakAdvisoryParams.length > 0 && (
          <div className="mb-5">
            <div className="flex items-center gap-2 mb-1">
              <h2 className="font-display text-base font-bold tracking-tight">Advisory</h2>
              <span className="text-[10px] font-bold tracking-wider uppercase px-2 py-0.5 rounded-md border border-border text-foreground/30 font-body">Does not affect final score</span>
            </div>
            <p className="text-foreground/30 text-[14px] mb-2.5 font-body">Suggestions to consider — not critical fixes</p>
            <div className="flex flex-col gap-3">
              {weakAdvisoryParams.map((param, i) => {
                const badge = severityBadge(param.score)
                return (
                  <div key={i} className={`border rounded-xl px-4 py-3.5 opacity-80 ${param.score < 40 ? 'border-red-200/50 bg-red-50/30' : param.score < 55 ? 'border-amber-200/50 bg-amber-50/30' : param.score < 70 ? 'border-amber-200/40 bg-amber-50/20' : 'border-green-200/50 bg-green-50/20'}`}
                    style={{ opacity: 0, animation: `fade-slide-in 0.4s ease-out ${i * 50}ms forwards` }}>
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="text-[13px] font-display font-bold text-foreground/60">{param.name}</span>
                      <div className="flex items-center gap-2">
                        <span className={`text-[9px] font-bold tracking-wider uppercase px-2 py-0.5 rounded-md border ${badge.bg}`}>{badge.label}</span>
                        <span className={`text-[13px] font-bold font-display ${scoreTextColor(param.score)}`}>{param.score}</span>
                      </div>
                    </div>
                    {(param.problem || param.whatIsHappening) && (
                      <p className={`text-[12px] font-body leading-relaxed mb-1 ${param.score < 40 ? 'text-red-700/65' : param.score < 70 ? 'text-amber-700/65' : 'text-foreground/40'}`}>{param.problem || param.whatIsHappening}</p>
                    )}
                    {(param.fix || param.whatToChange) && (
                      <p className="text-[12px] font-body text-foreground/60 leading-relaxed">
                        <span className="font-semibold text-foreground/50">Suggestion: </span>{param.fix || param.whatToChange}
                      </p>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* ═══ NEXT VIDEO BLUEPRINT — visible for pro/plus/creator sharer plans ═══ */}
        {isPaidSharer && isCompetitor && result.nextVideoTemplate && (
          <div className="border border-emerald-200/30 rounded-2xl overflow-hidden mb-5"
            style={{ opacity: 0, animation: 'fade-slide-in 0.6s ease-out 150ms forwards' }}>
            <div className="bg-gradient-to-r from-emerald-500/10 via-emerald-400/5 to-transparent px-5 sm:px-6 py-5 border-b border-emerald-200/25">
              <div className="flex items-center gap-3 mb-1">
                <div className="w-8 h-8 rounded-lg bg-emerald-100 border border-emerald-200/60 flex items-center justify-center">
                  <svg className="w-4 h-4 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15a2.25 2.25 0 012.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25zM6.75 12h.008v.008H6.75V12zm0 3h.008v.008H6.75V15zm0 3h.008v.008H6.75V18z" />
                  </svg>
                </div>
                <div>
                  <h2 className="font-display text-lg font-bold tracking-tight">Your Next Video Blueprint</h2>
                  <p className="text-[12px] text-emerald-700/40 font-body">Based on what worked — here's exactly what to create</p>
                </div>
              </div>
            </div>
            <div className="px-3 sm:px-5 py-3 sm:py-4 flex flex-col gap-1.5">
              {[
                { icon: '⚡', label: 'Hook', value: result.nextVideoTemplate.hook, color: 'border-amber-300/40', bgOpen: 'bg-amber-50/30', accent: 'text-amber-700/50' },
                { icon: '🎬', label: 'Structure', value: result.nextVideoTemplate.structure, color: 'border-blue-300/30', bgOpen: 'bg-blue-50/20', accent: 'text-blue-700/50' },
                { icon: '🎵', label: 'Audio', value: result.nextVideoTemplate.audio, color: 'border-purple-300/30', bgOpen: 'bg-purple-50/20', accent: 'text-purple-700/50' },
                { icon: '📍', label: 'Location & Setup', value: result.nextVideoTemplate.location, color: 'border-rose-300/30', bgOpen: 'bg-rose-50/20', accent: 'text-rose-700/50' },
                { icon: '✏️', label: 'Text Overlays', value: result.nextVideoTemplate.text, color: 'border-orange-300/30', bgOpen: 'bg-orange-50/20', accent: 'text-orange-700/50' },
                { icon: '📣', label: 'Call to Action', value: result.nextVideoTemplate.cta, color: 'border-emerald-300/30', bgOpen: 'bg-emerald-50/20', accent: 'text-emerald-700/50' },
              ].filter(item => item.value).map((item, i) => {
                const isOpen = expandedBlueprint.has(i)
                return (
                  <div key={i} className={`rounded-xl border overflow-hidden transition-all duration-200 ${item.color} ${isOpen ? item.bgOpen : 'bg-transparent'}`}
                    style={{ opacity: 0, animation: `fade-slide-in 0.4s ease-out ${i * 60}ms forwards` }}>
                    <button
                      onClick={() => setExpandedBlueprint(prev => { const n = new Set(prev); n.has(i) ? n.delete(i) : n.add(i); return n })}
                      className="w-full flex items-center gap-3 px-3.5 py-3 cursor-pointer hover:brightness-[0.97] transition-all"
                    >
                      <span className="text-base shrink-0">{item.icon}</span>
                      <span className={`text-[11px] sm:text-[12px] font-bold tracking-wider uppercase flex-1 text-left ${item.accent}`}>{item.label}</span>
                      <ChevronDown className={`w-3.5 h-3.5 text-foreground/25 transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`} />
                    </button>
                    <div className={`grid transition-all duration-200 ease-in-out ${isOpen ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}>
                      <div className="overflow-hidden">
                        <div className="px-3.5 pb-3.5 pt-0 pl-[3.25rem]">
                          <p className="text-[13px] text-foreground/65 font-body leading-[1.7]">{item.value}</p>
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* ═══ CAPTION & HASHTAG SECTION — visible for paid sharer plans ═══ */}
        {isPaidSharer && (hasTrendingSection || hasUploadCaptionSection) && (
          <div className="border border-border rounded-2xl p-5 mb-5"
            style={{ opacity: 0, animation: 'fade-slide-in 0.6s ease-out 200ms forwards' }}>
            <h2 className="font-display text-base font-bold tracking-tight mb-0.5">
              {isCompetitor ? 'Their Strategy' : isUpload ? 'Caption & Hashtags' : `Trending for ${result.niche}`}
            </h2>
            <p className="text-foreground/30 text-[14px] mb-4 font-body">
              {isCompetitor ? 'Hashtags and caption strategy this creator used' : isUpload ? 'Captions and trending hashtags tailored to your content — ready to copy and paste' : `Real data from ${result.platform}`}
            </p>

            {result.suggestedHashtags?.length > 0 && (
              <div className="mb-4">
                <div className="flex items-center justify-between mb-2">
                  <p className="text-[11px] font-semibold tracking-widest uppercase text-foreground/30">Suggested Hashtags</p>
                  <button onClick={() => copyText(result.suggestedHashtags.map(h => `#${h}`).join(' '), 'hashtags')}
                    className="text-[14px] text-foreground/30 hover:text-foreground/50 cursor-pointer transition-colors font-body underline">
                    {copied === 'hashtags' ? 'Copied!' : 'Copy all'}
                  </button>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {result.suggestedHashtags.map((tag, i) => (
                    <span key={i} className="px-2 py-0.5 rounded-full bg-secondary/60 border border-border text-[14px] font-body text-foreground/50">
                      #{tag}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {result.suggestedCaption && (
              <div className="mb-4">
                <div className="flex items-center justify-between mb-2">
                  <p className="text-[11px] font-semibold tracking-widest uppercase text-foreground/30">Suggested Caption</p>
                  <button onClick={() => copyText(result.suggestedCaption, 'caption')}
                    className="text-[14px] text-foreground/30 hover:text-foreground/50 cursor-pointer transition-colors font-body underline">
                    {copied === 'caption' ? 'Copied!' : 'Copy'}
                  </button>
                </div>
                <div className="bg-secondary/30 border border-border rounded-xl p-3.5">
                  <p className="text-[14px] text-foreground/60 font-body leading-relaxed whitespace-pre-wrap">{result.suggestedCaption}</p>
                </div>
              </div>
            )}

            {result.trendingAudioSuggestion && (
              <div>
                <p className="text-[11px] font-semibold tracking-widest uppercase text-foreground/30 mb-1.5">Audio Suggestion</p>
                <p className="text-[14px] text-foreground/55 font-body leading-relaxed">{result.trendingAudioSuggestion}</p>
              </div>
            )}
          </div>
        )}

        {/* ═══ WATERMARK ═══ */}
        {(result as any)?.sharer_username && (
          <p className="mt-6 text-center text-[11px] text-foreground/25 font-body">
            Analysed by @{(result as any).sharer_username} using Prysm
          </p>
        )}

        {/* ═══ CTA FOOTER ═══ */}
        <div className="mt-4 mb-4 text-center">
          <div className="border border-border rounded-2xl p-6 sm:p-8 bg-foreground/[0.015]">
            <h3 className="font-display text-lg font-bold tracking-tight mb-2">Want to analyse your own videos?</h3>
            <p className="text-foreground/40 font-body text-sm mb-5 max-w-md mx-auto">
              Get a 10-parameter breakdown with step-by-step fixes, tailored to your niche and style.
            </p>
            <a href="/register"
              className="inline-block px-8 py-3 rounded-xl bg-foreground text-background text-sm font-display font-semibold hover:opacity-90 transition-opacity">
              Get started free
            </a>
          </div>
        </div>

      </div>
    </div>
  )
}
