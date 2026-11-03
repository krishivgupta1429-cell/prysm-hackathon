import { useEffect, useState, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { getVideoUrl } from '@/lib/videoStore'
import { getUser, getToken, fetchUser } from '@/lib/auth'
import { apiUrl } from '@/lib/api'
import { canAccess, getNextPlan } from '@/lib/plans'
import {
  Zap, Eye, Music, Scissors, Type, MousePointerClick,
  TrendingUp, Image, MessageSquare, Bookmark, RotateCcw, Share2,
  ChevronDown, Check, Lightbulb, Link2, CheckCircle2, Lock,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { STUDIO_MODE } from '@/lib/config'
import CostPanel from '@/studio/CostPanel'
import ViewerBehaviourCard from '@/studio/ViewerBehaviourCard'

interface Parameter {
  name: string
  weight: number
  score: number
  advisory: boolean
  problem: string
  fix: string
  whatIsHappening: string
  whyItMatters: string
  whatToChange: string
  howToDoIt: string
  whatToExpect: string
  referenceFrame?: number
  label?: string | null   // Trend Alignment: "grounded (live trends)" or "estimate (no live trend data)"
  frameAnnotation?: string
}

interface Frame {
  timestamp: number
  base64: string
}

interface VisionFrame {
  timestamp: number
  facePresent?: boolean
  emotion?: string
  sceneType?: string
  onScreenText?: string
  visualHookStrength?: number
  issues?: string[]
  strengths?: string[]
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
  postMetadata?: any
  trendData?: any
  energyCurve?: EnergyCurve
  frames?: Frame[]
  first_frame_b64?: string
  nextVideoTemplate?: {
    hook?: string
    structure?: string
    audio?: string
    location?: string
    text?: string
    cta?: string
    caption?: string
  }
  visionData?: {
    frames?: VisionFrame[]
    hookMoment?: number
    bestThumbnailFrame?: number
    overallVisualScore?: number
    visualSummary?: string
  }
  engagementPrediction?: {
    available?: boolean
    reason?: string | null
    nawp: number | null
    ecr: number | null
    hook_risk: 'low' | 'medium' | 'high' | null
    retention_risk: 'low' | 'medium' | 'high' | null
    source: 'runpod' | 'heuristic' | 'evqa' | 'qwen_zero_shot'
    interpretation: string
    qalign?: { quality: number; aesthetic: number; band: string } | null
  }
  analysis_id?: string
  edited_video_key?: string
  edit_plan?: { applied_params: string[]; skipped_params: string[] }
  modelsUsed?: { step: string; model: string; status: string; note?: string }[]
}

/* ── Helpers ───────────────────────────────────────────────────────────────── */

function severityBadge(score: number) {
  if (score < 40) return { label: 'CRITICAL', bg: 'bg-red-100 text-red-700 border-red-200' }
  if (score < 55) return { label: 'NEEDS WORK', bg: 'bg-amber-100 text-amber-700 border-amber-200' }
  if (score < 70) return { label: 'IMPROVE', bg: 'bg-amber-50 text-amber-600 border-amber-100' }
  if (score < 85) return { label: 'GOOD', bg: 'bg-green-50 text-green-600 border-green-100' }
  return { label: 'VIRAL-READY', bg: 'bg-green-100 text-green-700 border-green-200' }
}

function estimateFixTime(name: string, score: number): string {
  if (name.includes('Caption') || name.includes('Hashtag')) return '2 min'
  if (name.includes('Text') || name.includes('On-Screen')) return '3 min'
  if (name.includes('Hook')) return '5 min'
  if (name.includes('Audio')) return '5 min'
  if (name.includes('Trend')) return '3 min'
  if (score < 40) return '5 min'
  return '3 min'
}

function scoreBarColor(score: number) {
  if (score >= 85) return 'bg-green-500/80'
  if (score >= 70) return 'bg-green-400/60'
  if (score >= 50) return 'bg-amber-500/70'
  return 'bg-red-500/60'
}

function scoreTextColor(score: number) {
  if (score >= 85) return 'text-green-600'
  if (score >= 70) return 'text-green-500/70'
  if (score >= 50) return 'text-amber-600'
  return 'text-red-500'
}

function cardBorderColor(score: number) {
  if (score < 40) return 'border-l-red-500'
  if (score < 55) return 'border-l-amber-500'
  return 'border-l-amber-400'
}

function parseSteps(text: string): string[] {
  if (!text) return []

  // Each step must be a complete sentence — starting with a capital letter,
  // ending with terminal punctuation. Splitting mid-sentence (at commas,
  // parentheses, or arbitrary newlines) produces nonsensical bullets.

  // 1) Numbered list: "1. Do this  2. Do that  3. Do another"
  //    Works whether separator is newline or space.
  const numberedParts = text.split(/(?:^|\s)\d+[\.\)]\s+/).map(s => s.trim()).filter(Boolean)
  if (numberedParts.length >= 2) {
    return numberedParts
      .map(s => s.replace(/\.\s*$/, '').trim())
      .filter(s => s.length > 8)
  }

  // 2) Newline list — only accept if EVERY line ends with terminal punctuation.
  //    Otherwise it's a wrapped paragraph, not a list.
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

  // 3) Arrow-separated: "Open app → Add text → Export"
  const byArrow = text.split(/\s*→\s*/).filter(s => s.trim().length > 5)
  if (byArrow.length >= 3) return byArrow.map(s => s.replace(/\.$/, '').trim())

  // 4) Sentence splitting — split on ". " followed by a capital letter so we
  //    don't break on decimals (2700K-3000K) or abbreviations (e.g.).
  //    Also normalise stray newlines to single spaces first so wrapped
  //    paragraphs don't produce mid-sentence breaks.
  const normalised = text.replace(/\s*\n+\s*/g, ' ').trim()
  const sentences = normalised.split(/(?<=[.!?])\s+(?=[A-Z])/)
    .map(s => s.trim())
    .filter(s => s.length > 10)
    .map(s => s.replace(/[.!?]$/, ''))
  if (sentences.length >= 2 && sentences.length <= 8) return sentences

  // Single step fallback
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

function severityGradient(score: number) {
  if (score < 40) return 'from-red-500/10 to-red-500/[0.02]'
  if (score < 55) return 'from-amber-500/10 to-amber-500/[0.02]'
  if (score < 70) return 'from-amber-400/8 to-amber-400/[0.01]'
  if (score < 85) return 'from-green-400/6 to-green-400/[0.01]'
  return 'from-green-500/8 to-green-500/[0.01]'
}

function severityDotColor(score: number) {
  if (score < 40) return 'bg-red-500'
  if (score < 55) return 'bg-amber-500'
  if (score < 70) return 'bg-amber-400'
  if (score < 85) return 'bg-green-400'
  return 'bg-green-500'
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K'
  return String(n)
}

function formatTimestamp(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

/* ── Video Player ──────────────────────────────────────────────────────── */

function VideoPlayer({ videoUrl, frames, hookMoment, firstFrameB64 }: {
  videoUrl: string | null
  frames: Frame[]
  hookMoment?: number
  firstFrameB64?: string
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [currentFrameIdx, setCurrentFrameIdx] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)

  // Frame slideshow auto-advance
  useEffect(() => {
    if (videoUrl || !isPlaying || frames.length === 0) return
    const interval = setInterval(() => {
      setCurrentFrameIdx(prev => (prev + 1) % frames.length)
    }, 1500)
    return () => clearInterval(interval)
  }, [isPlaying, videoUrl, frames.length])

  // Real video player
  if (videoUrl) {
    return (
      <div className="relative rounded-xl overflow-hidden bg-black">
        <video
          ref={videoRef}
          src={videoUrl}
          className="w-full object-contain bg-black"
          style={{ maxHeight: 'calc(100vh - 280px)' }}
          controls
          playsInline
        />
      </div>
    )
  }

  // First-frame thumbnail — only when there are no frames to show as slideshow
  if (firstFrameB64 && frames.length === 0) {
    return (
      <div className="relative rounded-xl overflow-hidden bg-black">
        <img
          src={`data:image/jpeg;base64,${firstFrameB64}`}
          alt="Video thumbnail"
          className="w-full object-contain bg-black"
          style={{ maxHeight: 'calc(100vh - 380px)' }}
        />
        <div className="absolute bottom-2 left-2 right-2 flex items-center justify-center">
          <span className="text-[10px] text-white/50 bg-black/50 rounded px-2 py-0.5 font-body backdrop-blur-sm">
            Preview — video available on the device used to analyse
          </span>
        </div>
      </div>
    )
  }

  // Frame slideshow fallback
  if (frames.length === 0) {
    return (
      <div className="rounded-xl bg-foreground/[0.03] border border-border flex items-center justify-center"
        style={{ height: 'calc(100vh - 380px)', minHeight: 300 }}>
        <div className="text-center px-6">
          <svg className="w-8 h-8 text-foreground/15 mx-auto mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 10.5l4.72-4.72a.75.75 0 011.28.53v11.38a.75.75 0 01-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 002.25-2.25v-9a2.25 2.25 0 00-2.25-2.25h-9A2.25 2.25 0 002.25 7.5v9a2.25 2.25 0 002.25 2.25z" />
          </svg>
          <p className="text-[12px] text-foreground/25 font-body">No video available</p>
          <p className="text-[11px] text-foreground/15 font-body mt-0.5">Upload a video to see playback</p>
        </div>
      </div>
    )
  }

  const currentFrame = frames[currentFrameIdx]
  return (
    <div className="relative rounded-xl overflow-hidden bg-black">
      <img
        src={`data:image/jpeg;base64,${currentFrame.base64}`}
        alt={`Frame at ${currentFrame.timestamp}s`}
        className="w-full object-contain bg-black"
        style={{ maxHeight: 'calc(100vh - 380px)' }}
      />
      {/* Timestamp badge */}
      <span className="absolute top-2 right-2 px-2 py-1 rounded-md bg-black/70 text-white text-[11px] font-body">
        {formatTimestamp(currentFrame.timestamp)}
      </span>

      {/* Scrubber bar */}
      <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 to-transparent p-2.5">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setIsPlaying(!isPlaying)}
            className="w-6 h-6 rounded-full bg-white/20 flex items-center justify-center cursor-pointer hover:bg-white/30 transition-colors"
          >
            {isPlaying ? (
              <svg className="w-2.5 h-2.5 text-white" fill="currentColor" viewBox="0 0 24 24"><path d="M6 4h4v16H6V4zm8 0h4v16h-4V4z" /></svg>
            ) : (
              <svg className="w-2.5 h-2.5 text-white ml-0.5" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
            )}
          </button>
          <div className="flex-1 flex gap-px">
            {frames.map((_, i) => (
              <button
                key={i}
                onClick={() => { setCurrentFrameIdx(i); setIsPlaying(false) }}
                className="flex-1 h-1 rounded-full cursor-pointer transition-all"
                style={{ background: i === currentFrameIdx ? 'white' : 'rgba(255,255,255,0.25)' }}
              />
            ))}
          </div>
          <span className="text-[11px] text-white/40 font-body">{currentFrameIdx + 1}/{frames.length}</span>
        </div>
      </div>
    </div>
  )
}

/* ── Audio Energy Curve ─────────────────────────────────────────────── */

function AudioEnergyCurve({ energy }: { energy: EnergyCurve }) {
  if (!energy?.curve?.length) return null
  const pts = energy.curve
  const duration = energy.duration || pts[pts.length - 1]?.t || 1
  const low = energy.low_energy_segments || []

  // Build SVG path from energy points
  const W = 300
  const H = 56
  const pad = 4
  const innerW = W - pad * 2
  const innerH = H - pad * 2

  const toX = (t: number) => pad + (t / duration) * innerW
  const toY = (e: number) => pad + innerH - e * innerH

  const pathD = pts.map((p, i) =>
    `${i === 0 ? 'M' : 'L'} ${toX(p.t).toFixed(1)} ${toY(p.energy).toFixed(1)}`
  ).join(' ')

  const fillD = pathD + ` L ${toX(pts[pts.length - 1].t).toFixed(1)} ${(pad + innerH).toFixed(1)} L ${toX(pts[0].t).toFixed(1)} ${(pad + innerH).toFixed(1)} Z`

  return (
    <div className="shrink-0 border border-border rounded-xl p-3.5 bg-background">
      <div className="flex items-center justify-between mb-2.5">
        <p className="text-[10px] font-semibold tracking-widest uppercase text-foreground/30">Audio Energy</p>
        {energy.drop_detected && (
          <span className="text-[9px] font-bold tracking-wider uppercase px-1.5 py-0.5 rounded border border-red-200/50 bg-red-50/40 text-red-600/70">
            Drop at {energy.drop_at_seconds}s
          </span>
        )}
      </div>

      {/* SVG waveform */}
      <div className="relative w-full overflow-hidden rounded-lg" style={{ height: H }}>
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-full" preserveAspectRatio="none">
          {/* Low energy segment shading */}
          {low.map((seg, i) => (
            <rect
              key={i}
              x={toX(seg.start)}
              y={pad}
              width={toX(seg.end) - toX(seg.start)}
              height={innerH}
              fill="rgba(239,68,68,0.08)"
            />
          ))}
          {/* Fill under curve */}
          <path d={fillD} fill="rgba(34,197,94,0.08)" />
          {/* Curve line */}
          <path d={pathD} fill="none" stroke="rgba(34,197,94,0.6)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          {/* Drop markers */}
          {low.map((seg, i) => (
            <line
              key={i}
              x1={toX(seg.start)} y1={pad}
              x2={toX(seg.start)} y2={pad + innerH}
              stroke="rgba(239,68,68,0.35)" strokeWidth="1" strokeDasharray="2,2"
            />
          ))}
        </svg>
      </div>

      {/* Zone labels */}
      <div className="flex items-center justify-between mt-2">
        <div className="text-center">
          <p className="text-[9px] text-foreground/25 font-body">Hook</p>
          <p className={`text-[11px] font-bold font-display ${energy.hook_energy >= 0.6 ? 'text-green-600/70' : 'text-amber-600/70'}`}>
            {Math.round(energy.hook_energy * 100)}%
          </p>
        </div>
        <div className="text-center">
          <p className="text-[9px] text-foreground/25 font-body">Mid</p>
          <p className={`text-[11px] font-bold font-display ${energy.mid_energy >= 0.5 ? 'text-green-600/70' : 'text-amber-600/70'}`}>
            {Math.round(energy.mid_energy * 100)}%
          </p>
        </div>
        <div className="text-center">
          <p className="text-[9px] text-foreground/25 font-body">Closing</p>
          <p className={`text-[11px] font-bold font-display ${energy.closing_energy >= 0.5 ? 'text-green-600/70' : 'text-amber-600/70'}`}>
            {Math.round(energy.closing_energy * 100)}%
          </p>
        </div>
        <div className="text-center">
          <p className="text-[9px] text-foreground/25 font-body">Avg</p>
          <p className="text-[11px] font-bold font-display text-foreground/40">
            {Math.round(energy.avg_energy * 100)}%
          </p>
        </div>
      </div>

      {/* Low energy warnings */}
      {low.length > 0 && (
        <div className="mt-2.5 flex flex-col gap-1">
          {low.slice(0, 2).map((seg, i) => (
            <p key={i} className="text-[10px] text-red-600/60 font-body">
              ↓ Low energy {seg.start}s–{seg.end}s — viewer drop-off risk
            </p>
          ))}
        </div>
      )}
    </div>
  )
}

/* ══════════════════════════════════════════════════════════════════════════ */
/*  MAIN COMPONENT                                                          */
/* ══════════════════════════════════════════════════════════════════════════ */

// `injected` (hackathon, local /dev/bakeoff page only): render this result instead of the one in localStorage; no video, no network side effects.
export default function Results({ injected }: { injected?: Record<string, unknown> } = {}) {
  const [result, setResult] = useState<Result | null>(null)
  const [videoUrl, setVideoUrl] = useState<string | null>(null)
  const [expandedFix, setExpandedFix] = useState<number | null>(null)
  const [expandedWhy, setExpandedWhy] = useState<Set<number>>(new Set())
  const [expandedBlueprint, setExpandedBlueprint] = useState<Set<number>>(new Set())
  const [checkedSteps, setCheckedSteps] = useState<Set<string>>(new Set())
  const [copied, setCopied] = useState<string | null>(null)
  const [scoreAnimated, setScoreAnimated] = useState(0)
  const [shareState, setShareState] = useState<'idle' | 'loading' | 'copied'>('idle')
  // verifiedPlan is fetched fresh from the backend on every Results page load.
  // This prevents the exploit where visiting /pricing and going back would
  // unlock gated content because localStorage held a stale/upgraded plan value.
  const [verifiedPlan, setVerifiedPlan] = useState<string | undefined>(getUser()?.plan)
  const navigate = useNavigate()

  const handleShare = useCallback(async () => {
    const stored = localStorage.getItem('prysm_result')
    if (!stored) return
    const parsed = JSON.parse(stored)
    const analysisId = parsed.analysis_id
    const token = getToken()
    if (!analysisId || !token) return

    setShareState('loading')
    try {
      const res = await fetch(apiUrl(`/prysm/share/${analysisId}`), {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Failed to create share link')
      const data = await res.json()
      const shareUrl = `${window.location.origin}/share/${data.share_id}`

      // Try native share sheet on mobile first, then clipboard, then execCommand fallback
      if (navigator.share) {
        await navigator.share({ title: 'My Prysm Analysis', url: shareUrl })
      } else {
        try {
          await navigator.clipboard.writeText(shareUrl)
        } catch {
          // execCommand fallback for browsers that block clipboard API
          const ta = document.createElement('textarea')
          ta.value = shareUrl
          ta.style.position = 'fixed'
          ta.style.opacity = '0'
          document.body.appendChild(ta)
          ta.focus()
          ta.select()
          document.execCommand('copy')
          document.body.removeChild(ta)
        }
      }

      setShareState('copied')
      setTimeout(() => setShareState('idle'), 3000)
    } catch {
      setShareState('idle')
    }
  }, [])

  useEffect(() => {
    // Always verify plan from backend — never trust localStorage alone for gating
    fetchUser().then(u => { if (u) setVerifiedPlan(u.plan) })

    const stored = injected ? JSON.stringify(injected) : localStorage.getItem('prysm_result')
    if (!stored) { navigate('/analyse'); return }
    try {
      const parsed = JSON.parse(stored)
      if (!parsed.topPriorities) parsed.topPriorities = []
      if (!parsed.parameters) parsed.parameters = []
      if (!parsed.scoreLabel) parsed.scoreLabel = 'Analysed'
      if (!parsed.summary) parsed.summary = 'Analysis complete.'
      if (!parsed.biggestProblem) parsed.biggestProblem = parsed.topPriorities?.[0] || ''
      if (!parsed.quickFix) parsed.quickFix = ''
      if (!parsed.suggestedHashtags) parsed.suggestedHashtags = []
      if (!parsed.suggestedCaption) parsed.suggestedCaption = ''
      if (!parsed.predictedScoreAfterFixes) parsed.predictedScoreAfterFixes = Math.min(parsed.overallScore + 15, 98)
      if (!parsed.frames) parsed.frames = []
      setResult(parsed)

      // Load video from IndexedDB (for uploads) or skip (for links).
      const source = injected ? null : localStorage.getItem('prysm_video_source')
      if (source === 'upload') {
        getVideoUrl(parsed.analysis_id).then(url => { if (url) setVideoUrl(url) })
      } else if (source === 'link' && parsed.analysis_id && (!parsed.frames || parsed.frames.length === 0)) {
        // For link analyses frames aren't in Redis cache — fetch from DB
        const tk = getToken()
        if (tk) {
          fetch(apiUrl(`/prysm/dashboard/${parsed.analysis_id}/frames`), {
            headers: { Authorization: `Bearer ${tk}` },
          })
            .then(r => r.ok ? r.json() : null)
            .then(data => {
              if (data?.frames?.length > 0) {
                setResult(prev => prev ? { ...prev, frames: data.frames } : prev)
              }
            })
            .catch(() => {})
        }
      }

      // Spring animation — overshoot then settle
      let current = 0
      const target = parsed.overallScore
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
    } catch { navigate('/analyse') }
  }, [])

  // Cleanup blob URL on unmount
  useEffect(() => {
    return () => { if (videoUrl) URL.revokeObjectURL(videoUrl) }
  }, [videoUrl])

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
  function toggleStep(fixIdx: number, stepIdx: number) {
    const key = `${fixIdx}-${stepIdx}`
    setCheckedSteps(prev => { const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n })
  }

  if (!result) return (
    <div className="min-h-screen bg-background flex items-center justify-center">
      <p className="text-foreground/30 font-body">Loading...</p>
    </div>
  )

  // Plan gating — always uses verifiedPlan (fetched from backend on load, not localStorage)
  const userPlan = verifiedPlan
  const isFreeUser = !STUDIO_MODE && (!userPlan || userPlan.toLowerCase() === 'free')
  const canSeeCaptions = canAccess.captionsAndHashtags(userPlan)
  const canSeeBlueprint = canAccess.videoBlueprint(userPlan)
  const canSeeAudioCurve = canAccess.audioEnergyCurve(userPlan)
  const canSeePrediction = canAccess.predictedViewerBehaviour(userPlan)
  const nextPlan = getNextPlan(userPlan)
  // Free users see only the first 2 scored parameters (Hook & Opening, Retention & Pacing)
  const FREE_VISIBLE_COUNT = 2

  // Intent and source type: from result data or localStorage fallback
  const intent = result.intent || localStorage.getItem('prysm_intent') || 'my_video'
  const sourceType = result.source_type || localStorage.getItem('prysm_video_source') || 'upload'
  const isCompetitor = intent === 'competitor'
  const isUpload = sourceType === 'upload'
  const isOwnLink = !isUpload && !isCompetitor

  // For uploads: separate Caption & Hashtag from scored params (shown greyed out in bar chart, excluded from Fix section)
  const isCaptionHashtagParam = (p: Parameter) => p.name.toLowerCase().includes('caption') || p.name.toLowerCase().includes('hashtag')
  const scoredParams = isUpload ? result.parameters.filter((p: Parameter) => !isCaptionHashtagParam(p)) : result.parameters
  const captionParam = isUpload ? result.parameters.find((p: Parameter) => isCaptionHashtagParam(p)) : null

  // Advisory parameters — informational only, do not affect overall score.
  // Fall back to name-matching for old cached results that pre-date the advisory field.
  const ADVISORY_NAMES = new Set(['Call to Action', 'On-Screen Text', 'Save Potential', 'Replay Value'])
  const isAdvisory = (p: Parameter) => p.advisory === true || ADVISORY_NAMES.has(p.name)
  const mainParams = scoredParams.filter(p => !isAdvisory(p))
  const advisoryParams = scoredParams.filter(p => isAdvisory(p))

  const weakParams = [...mainParams].filter(p => p.score < 85).sort((a, b) => a.score - b.score)
  const strongParams = [...mainParams].filter(p => p.score >= 85).sort((a, b) => b.score - a.score)
  const weakAdvisoryParams = [...advisoryParams].sort((a, b) => a.score - b.score)

  const mainParamsSorted = isCompetitor
    ? [...mainParams].sort((a, b) => b.score - a.score)
    : [...mainParams].sort((a, b) => a.score - b.score)
  const advisoryParamsSorted = [...advisoryParams].sort((a, b) => a.score - b.score)
  // For the bar chart: show all params but put caption at the end for uploads
  const allParamsSorted = isCompetitor
    ? [...scoredParams].sort((a, b) => b.score - a.score)
    : [...scoredParams].sort((a, b) => a.score - b.score)
  const hasTrendingSection = (!isUpload && (result.suggestedHashtags?.length > 0 || result.suggestedCaption || result.trendingAudioSuggestion))
  const hasUploadCaptionSection = isUpload && (result.suggestedHashtags?.length > 0 || result.suggestedCaption)
  const hasPostData = result.postMetadata && (result.postMetadata.likeCount > 0 || result.postMetadata.viewCount > 0)

  const circumference = 2 * Math.PI * 54
  const offset = circumference - (scoreAnimated / 100) * circumference
  const ringColor = result.overallScore >= 85 ? 'stroke-green-500'
    : result.overallScore >= 70 ? 'stroke-green-400'
    : result.overallScore >= 55 ? 'stroke-amber-400'
    : result.overallScore >= 40 ? 'stroke-orange-400'
    : 'stroke-red-500'

  const hookMoment = result.visionData?.hookMoment
  const frames = result.frames || []
  const paramCount = result.parameters.length || (scoredParams.length + (captionParam ? 1 : 0))

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* ═══ NAV ═══ */}
      <nav className="border-b border-border px-3 sm:px-8 h-14 flex items-center justify-between sticky top-0 bg-background/90 backdrop-blur-md z-50">
        <a href="/" className="font-logo text-[18px] sm:text-[21px] text-foreground">PRYSM</a>
        <div className="flex items-center gap-2 sm:gap-4">
          {getToken() && (
            <button
              onClick={handleShare}
              disabled={shareState === 'loading'}
              className="flex items-center gap-1.5 text-xs sm:text-sm text-foreground/30 hover:text-foreground/50 transition-colors cursor-pointer disabled:opacity-50"
            >
              {shareState === 'copied' ? (
                <><CheckCircle2 className="w-3.5 h-3.5 text-green-500" /> <span className="text-green-500">Link copied!</span></>
              ) : shareState === 'loading' ? (
                <><div className="w-3 h-3 border border-foreground/20 border-t-foreground/50 rounded-full animate-spin" /> Sharing...</>
              ) : (
                <><Link2 className="w-3.5 h-3.5" /> Share</>
              )}
            </button>
          )}
          <a href="/dashboard" className="text-xs sm:text-sm text-foreground/30 hover:text-foreground/50 transition-colors">Dashboard</a>
          <a href="/analyse" className="text-xs sm:text-sm text-foreground/40 hover:text-foreground/60 transition-colors">New Analysis →</a>
        </div>
      </nav>

      {/* ═══ TWO-COLUMN LAYOUT ═══ */}
      <div className="max-w-7xl mx-auto px-3 sm:px-6 py-4 sm:py-6">
        <div className="flex gap-4 sm:gap-6 lg:flex-row flex-col">

          {/* ─── LEFT COLUMN: Video + Scene Map (sticky, fits viewport) ─── */}
          <div className="lg:w-[380px] shrink-0">
            <div className="lg:sticky lg:top-[72px] flex flex-col gap-3">
              {/* Video player */}
              <div className="shrink-0">
                <VideoPlayer
                  videoUrl={videoUrl}
                  frames={frames}
                  hookMoment={hookMoment}
                  firstFrameB64={result.first_frame_b64}
                />
              </div>



              {/* Audio Energy Curve — Pro+ */}
              {result.energyCurve?.curve?.length ? (
                canSeeAudioCurve ? (
                  <AudioEnergyCurve energy={result.energyCurve} />
                ) : (
                  <div className="shrink-0 border border-border rounded-xl p-4 bg-background relative overflow-hidden">
                    <div className="flex items-center justify-between mb-3">
                      <p className="text-[10px] font-semibold tracking-widest uppercase text-foreground/30">Audio Energy</p>
                      <Lock className="w-3 h-3 text-foreground/20" />
                    </div>
                    {/* Opaque placeholder bars — no real data visible */}
                    <div className="flex items-end gap-0.5 h-12">
                      {[40, 55, 35, 60, 45, 70, 50, 38, 62, 48, 55, 42, 65, 52, 44].map((_, idx) => (
                        <div key={idx} className="flex-1 rounded-sm bg-foreground/[0.06]" style={{ height: '100%' }} />
                      ))}
                    </div>
                    <div className="mt-3 flex items-center justify-between">
                      <p className="text-[11px] text-foreground/30 font-body">Audio drop detection & energy curve</p>
                      <a href="/pricing" className="text-[11px] font-semibold text-foreground/50 hover:text-foreground transition-colors font-body">Pro →</a>
                    </div>
                  </div>
                )
              ) : null}

              {/* Post Performance */}
              {hasPostData && (
                <div className="shrink-0 border border-border rounded-xl p-4 bg-background">
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
            </div>
          </div>

          {/* ─── RIGHT COLUMN: All analysis content (scrollable) ─── */}
          <div className="flex-1 min-w-0">

            {/* ═══ SCORE CARD ═══ */}
            <div className="border border-border rounded-2xl p-4 sm:p-6 mb-5 overflow-visible"
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
                  <div className="inline-flex items-center gap-1.5 mb-2">
                    <span className={`text-[9px] sm:text-[10px] font-bold tracking-widest uppercase px-2.5 py-0.5 rounded-full border ${severityBadge(result.overallScore).bg}`}>
                      {result.scoreLabel}
                    </span>
                    {/* Score bands tooltip */}
                    <div className="relative group/bands">
                      <button className="w-4 h-4 rounded-full border border-foreground/15 bg-foreground/[0.04] flex items-center justify-center text-foreground/30 hover:text-foreground/60 hover:border-foreground/30 transition-colors cursor-default text-[9px] font-bold leading-none">
                        ?
                      </button>
                      <div className="absolute left-1/2 -translate-x-1/2 top-full mt-2 opacity-0 group-hover/bands:opacity-100 pointer-events-none transition-opacity duration-150 z-[200] w-[210px]">
                        {/* Arrow */}
                        <div className="flex justify-center mb-0">
                          <div className="w-2.5 h-2.5 bg-foreground rotate-45 -mb-1.5 relative z-10" />
                        </div>
                        <div className="bg-foreground text-background rounded-xl p-3 shadow-2xl relative">
                          <p className="text-[9px] font-bold tracking-widest uppercase opacity-40 mb-2.5">Score bands</p>
                          <div className="flex flex-col gap-2">
                            {[
                              { range: '85 – 100', label: 'Viral Ready',     color: 'bg-green-500' },
                              { range: '70 – 84',  label: 'Strong',          color: 'bg-green-400' },
                              { range: '55 – 69',  label: 'Good Potential',  color: 'bg-amber-400' },
                              { range: '40 – 54',  label: 'Needs Work',      color: 'bg-orange-400' },
                              { range: '0 – 39',   label: 'Critical Issues', color: 'bg-red-500' },
                            ].map(({ range, label, color }) => (
                              <div key={range} className="flex items-center gap-2">
                                <div className={`w-2 h-2 rounded-full shrink-0 ${color}`} />
                                <span className="text-[11px] font-body opacity-80 flex-1">{label}</span>
                                <span className="text-[10px] font-body opacity-40 shrink-0">{range}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
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

              {/* Post readiness for uploads */}
              {isUpload && weakParams.length > 0 && (
                <div className="bg-amber-50/30 border border-amber-200/30 rounded-xl p-3 mb-4">
                  <p className="text-[14px] text-amber-700/60 font-body text-center">
                    {weakParams.length} fix{weakParams.length !== 1 ? 'es' : ''} before this is ready to post
                  </p>
                </div>
              )}
              {isUpload && weakParams.length === 0 && (
                <div className="bg-green-50/30 border border-green-200/30 rounded-xl p-3 mb-4">
                  <p className="text-[14px] text-green-700/60 font-body text-center">
                    Every parameter is viral-ready — this video is good to post
                  </p>
                </div>
              )}

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
              <p className="text-foreground/30 text-[12px] mb-4 font-body">{isCompetitor ? 'How well this video executes each signal — high scores = techniques worth learning' : `Based on ${result.platform}'s actual ranking signals`}</p>

              <div className="flex flex-col gap-2.5">
                {(isFreeUser
                  ? [...mainParams].sort((a, b) => a.score - b.score).slice(0, FREE_VISIBLE_COUNT)
                  : isCompetitor ? allParamsSorted : mainParamsSorted
                ).map((param, i) => {
                  const badge = severityBadge(param.score)
                  return (
                    <div key={i} className="flex items-center gap-3"
                      style={{ opacity: 0, animation: `fade-slide-in 0.4s ease-out ${i * 50}ms forwards` }}>
                      <span className="w-[140px] sm:w-[180px] shrink-0 text-[13px] font-body text-foreground/50 leading-tight">
                        {param.name}
                        {param.label && <span className="block text-[10px] text-foreground/30 mt-0.5">{param.label}</span>}
                      </span>
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
                {/* Free user locked parameters row — counts both remaining scored + all advisory */}
                {isFreeUser && (mainParamsSorted.length - FREE_VISIBLE_COUNT + (isCompetitor ? 0 : advisoryParamsSorted.length)) > 0 && (
                  <div className="flex items-center gap-3 mt-1 pt-3 border-t border-border/50"
                    style={{ opacity: 0, animation: `fade-slide-in 0.4s ease-out 200ms forwards` }}>
                    <div className="flex items-center gap-2 flex-1">
                      <Lock className="w-3.5 h-3.5 text-foreground/25 shrink-0" />
                      <span className="text-[13px] font-body text-foreground/30">
                        {mainParamsSorted.length - FREE_VISIBLE_COUNT + (isCompetitor ? 0 : advisoryParamsSorted.length)} more parameters locked
                      </span>
                    </div>
                    <a href="/pricing" className="text-[12px] font-semibold text-foreground/50 hover:text-foreground transition-colors font-body shrink-0">
                      Unlock with Pro →
                    </a>
                  </div>
                )}

                {/* Advisory parameters — Pro+ only */}
                {!isFreeUser && !isCompetitor && advisoryParamsSorted.length > 0 && (
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
                          <span className="w-[140px] sm:w-[180px] shrink-0 text-[13px] font-body text-foreground/50 leading-tight">
                        {param.name}
                        {param.label && <span className="block text-[10px] text-foreground/30 mt-0.5">{param.label}</span>}
                      </span>
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
                    style={{ opacity: 0, animation: `fade-slide-in 0.4s ease-out ${allParamsSorted.length * 50}ms forwards` }}>
                    <span className="w-[140px] sm:w-[180px] shrink-0 text-[13px] font-body text-foreground/25">Caption & Hashtags</span>
                    <div className="flex-1 h-2.5 bg-foreground/[0.03] rounded-full overflow-hidden">
                      {/* No fill — greyed out */}
                    </div>
                    <span className="w-7 text-right text-[14px] font-bold font-display text-foreground/20">—</span>
                    <button
                      onClick={() => document.getElementById('upload-caption-section')?.scrollIntoView({ behavior: 'smooth' })}
                      className="text-[10px] font-semibold tracking-wider uppercase px-2 py-1 rounded-md border border-border text-foreground/25 hover:text-foreground/40 cursor-pointer transition-colors shrink-0 min-w-[70px] text-center"
                    >
                      See below ↓
                    </button>
                  </div>
                )}
              </div>
            </div>

            {/* ═══ COST BREAKDOWN (local flag or founder only; renders nothing otherwise) ═══ */}
            <CostPanel analysisId={result.analysis_id} />

            {/* ═══ MODELS USED ═══ */}
            {result.modelsUsed && result.modelsUsed.length > 0 && (
              <details className="border border-border rounded-2xl px-5 py-3 mb-5">
                <summary className="cursor-pointer text-[12px] font-semibold tracking-widest uppercase text-foreground/40 font-body">Models used</summary>
                <ul className="mt-3 space-y-1.5">
                  {result.modelsUsed.map(m => (
                    <li key={m.step} className="flex items-baseline justify-between gap-4 text-[12px] font-body">
                      <span className="text-foreground/50">{m.step}</span>
                      <span className="text-foreground/35 text-right">
                        {m.model}
                        {m.note ? ` · ${m.note}` : ''}
                        {m.status === 'unavailable' || m.status === 'error' ? ' · not available for this video' : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              </details>
            )}

            {/* ═══ Hackathon: Modal-served prediction (evqa / qwen_zero_shot) + Q-Align visual quality signal ═══ */}
            {result.engagementPrediction && canSeePrediction && (result.engagementPrediction.source === 'evqa' || result.engagementPrediction.source === 'qwen_zero_shot') && (
              <ViewerBehaviourCard ep={result.engagementPrediction} />
            )}

            {/* ═══ ENGAGEMENT PREDICTION (RunPod AI) — Creator+ ═══ */}
            {result.engagementPrediction && result.engagementPrediction.source === 'runpod' && result.engagementPrediction.ecr != null && result.engagementPrediction.nawp != null && (
              canSeePrediction ? (() => {
                const ep = result.engagementPrediction!
                const ecrRiskColor = ep.hook_risk === 'low' ? 'text-green-600' : ep.hook_risk === 'medium' ? 'text-amber-500' : 'text-red-500'
                const nawpRiskColor = ep.retention_risk === 'low' ? 'text-green-600' : ep.retention_risk === 'medium' ? 'text-amber-500' : 'text-red-500'
                const ecrBar = ep.hook_risk === 'low' ? 'bg-green-500' : ep.hook_risk === 'medium' ? 'bg-amber-400' : 'bg-red-500'
                const nawpBar = ep.retention_risk === 'low' ? 'bg-green-500' : ep.retention_risk === 'medium' ? 'bg-amber-400' : 'bg-red-500'
                return (
                  <div className="border border-border rounded-2xl p-5 sm:p-6 mb-5"
                    style={{ opacity: 0, animation: 'fade-slide-in 0.6s ease-out 150ms forwards' }}>
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
                          <div className={`h-full rounded-full ${ecrBar}`} style={{ width: `${ep.ecr}%`, transition: 'width 1s ease-out 400ms' }} />
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
                          <div className={`h-full rounded-full ${nawpBar}`} style={{ width: `${ep.nawp}%`, transition: 'width 1s ease-out 500ms' }} />
                        </div>
                        <p className="text-[11px] text-foreground/30 font-body mt-1">% of the video the average viewer is predicted to watch</p>
                      </div>
                    </div>
                  </div>
                )
              })() : (
                <div className="border border-border rounded-2xl p-5 sm:p-6 mb-5 relative overflow-hidden"
                  style={{ opacity: 0, animation: 'fade-slide-in 0.6s ease-out 150ms forwards' }}>
                  <div className="flex items-center justify-between mb-1">
                    <h2 className="font-display text-base font-bold tracking-tight">Predicted Viewer Behaviour</h2>
                    <Lock className="w-4 h-4 text-foreground/20" />
                  </div>
                  <p className="text-foreground/30 text-[12px] mb-5 font-body">Hook Hold Rate + Average Watch Rate — powered by AI</p>
                  <div className="flex flex-col gap-4 pointer-events-none select-none">
                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-[13px] font-body text-foreground/30">Hook Hold Rate</span>
                        <div className="w-10 h-4 rounded-md bg-foreground/[0.06]" />
                      </div>
                      <div className="h-2 bg-foreground/[0.06] rounded-full" />
                    </div>
                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-[13px] font-body text-foreground/30">Average Watch Rate</span>
                        <div className="w-10 h-4 rounded-md bg-foreground/[0.06]" />
                      </div>
                      <div className="h-2 bg-foreground/[0.06] rounded-full" />
                    </div>
                  </div>
                  <div className="mt-5 flex items-center justify-between">
                    <p className="text-[11px] text-foreground/30 font-body">Available on Pro plan</p>
                    <a href="/pricing" className="text-[12px] font-semibold text-foreground/50 hover:text-foreground transition-colors font-body">Upgrade →</a>
                  </div>
                </div>
              )
            )}

            {/* ═══ FIX THIS FIRST / WHAT MADE THIS WORK ═══ */}
            {(isCompetitor ? scoredParams.length > 0 : weakParams.length > 0) && (() => {
              // Free users only see fixes for the 2 visible parameters (own video and competitor)
              const visibleWeakParams = isFreeUser
                ? weakParams.filter(p => mainParamsSorted.slice(0, FREE_VISIBLE_COUNT).some(vp => vp.name === p.name))
                : weakParams
              const allCompetitorParams = [...mainParams].sort((a, b) => b.score - a.score)
              const visibleCompetitorParams = isFreeUser
                ? [...mainParams].sort((a, b) => a.score - b.score).slice(0, FREE_VISIBLE_COUNT)
                : allCompetitorParams
              const displayParams = isCompetitor ? visibleCompetitorParams : visibleWeakParams
              const totalSteps = displayParams.reduce((acc, p) => acc + parseSteps(p.howToDoIt || p.fix || p.whatToChange).length, 0)
              const checkedCount = Array.from(checkedSteps).length
              const reviewedCount = displayParams.filter((_, i) => expandedFix === i || checkedSteps.has(`${i}-0`)).length
              return (
              <div className="mb-5">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h2 className="font-display text-base font-bold tracking-tight">
                      {isCompetitor
                        ? `Techniques That ${result.postMetadata?.likeCount ? `Got ${formatNumber(result.postMetadata.likeCount)} Likes` : 'Made This Go Viral'}`
                        : isUpload ? 'Fix before you post' : 'Fix This First'}
                    </h2>
                    {isCompetitor && (
                      <p className="text-[14px] text-foreground/30 font-body mt-0.5">Apply these to your content for similar results</p>
                    )}
                  </div>
                  {!isCompetitor && (
                    <span className="text-[14px] font-body font-semibold text-foreground/50 px-2.5 py-1 rounded-full border border-border bg-secondary/40">{visibleWeakParams.length} issues</span>
                  )}
                </div>

                {/* Progress bar */}
                {!isCompetitor && totalSteps > 0 && (
                  <div className="mb-4 rounded-xl border border-border/60 bg-secondary/20 p-3">
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="text-[12px] font-body font-semibold text-foreground/40">
                        {checkedCount} of {totalSteps} steps completed
                      </span>
                      <span className="text-[12px] font-body text-foreground/25">
                        {Math.round((checkedCount / totalSteps) * 100)}%
                      </span>
                    </div>
                    <div className="w-full h-2 bg-foreground/[0.04] rounded-full overflow-hidden">
                      <div className="h-full bg-green-500/60 rounded-full transition-all duration-500 ease-out"
                        style={{ width: `${(checkedCount / totalSteps) * 100}%` }} />
                    </div>
                  </div>
                )}

                <div className="flex flex-col gap-3">
                  {displayParams.map((param, i) => {
                    const badge = severityBadge(param.score)
                    const steps = parseSteps(param.howToDoIt || param.fix || param.whatToChange)
                    const timeEst = estimateFixTime(param.name, param.score)
                    const isCaptionParam = param.name.toLowerCase().includes('caption') || param.name.toLowerCase().includes('hashtag')
                    const isFixExpanded = expandedFix === i
                    const isWhyExpanded = expandedWhy.has(i)
                    const ParamIcon = getParamIcon(param.name)
                    const stepsChecked = steps.filter((_, j) => checkedSteps.has(`${i}-${j}`)).length
                    const allStepsChecked = steps.length > 0 && stepsChecked === steps.length

                    return (
                      <div key={i}
                        className={`border rounded-2xl overflow-hidden transition-all duration-300 ${allStepsChecked ? 'opacity-60' : ''} ${isCompetitor ? 'border-green-200/30' : ''}`}
                        style={{ opacity: 0, animation: `fade-slide-in 0.5s ease-out ${i * 80}ms forwards` }}>

                        {/* Gradient header — clickable to expand/collapse */}
                        <button
                          onClick={() => toggleFix(i)}
                          className={`w-full px-4 py-3.5 flex items-center gap-3 bg-gradient-to-r ${
                            isCompetitor
                              ? param.score >= 70 ? 'from-emerald-500/12 via-green-500/6 to-transparent' : param.score >= 50 ? 'from-amber-500/10 via-amber-400/4 to-transparent' : 'from-foreground/[0.04] to-transparent'
                              : severityGradient(param.score)
                          } border-b border-border/40 hover:brightness-[0.97] transition-all cursor-pointer`}
                        >
                          {/* Icon */}
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

                          {/* Name + badge */}
                          <div className="flex-1 min-w-0 text-left">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="font-display font-bold text-[13px] sm:text-[15px] text-foreground">{param.name}</span>
                              {param.advisory && (
                                <span className="text-[9px] font-semibold tracking-widest uppercase px-2 py-0.5 rounded-full border border-foreground/10 text-foreground/35">
                                  advisory · does not affect final score
                                </span>
                              )}
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
                              {/* Severity dot — pulsing for critical */}
                              {!isCompetitor && (
                                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${severityDotColor(param.score)} ${param.score < 40 ? 'animate-pulse' : ''}`} />
                              )}
                            </div>
                          </div>

                          {/* Score */}
                          <span className={`font-display font-bold text-base shrink-0 ${
                            isCompetitor
                              ? param.score >= 70 ? 'text-emerald-600' : param.score >= 50 ? 'text-amber-600' : 'text-foreground/40'
                              : scoreTextColor(param.score)
                          }`}>
                            {param.score}
                          </span>

                          {/* Chevron */}
                          <ChevronDown className={`w-4 h-4 text-foreground/25 shrink-0 transition-transform duration-300 ${isFixExpanded ? 'rotate-180' : ''}`} />
                        </button>

                        {/* Expanded content */}
                        <div className={`grid transition-all duration-300 ease-in-out ${isFixExpanded ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}>
                          <div className="overflow-hidden">
                            <div className="px-5 pt-4 pb-5">

                              {/* Problem / What they did */}
                              {isCompetitor ? (
                                <div className="mb-4 border-l-[3px] border-emerald-400/50 pl-4 py-1">
                                  <p className="text-[10px] font-bold tracking-widest uppercase text-emerald-700/50 mb-1.5">
                                    What they did
                                  </p>
                                  <p className="text-[14px] font-body leading-[1.7] text-foreground/75">
                                    {param.problem || param.whatIsHappening}
                                  </p>
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

                              {isCompetitor ? (
                                <div className="w-full h-px bg-emerald-200/30 mb-4" />
                              ) : (
                                <div className="w-full h-px bg-border/60 mb-4" />
                              )}

                              {/* Fix / Apply to your content */}
                              {isCompetitor ? (
                                <div className="mb-4 rounded-xl bg-emerald-50/40 border border-emerald-200/30 px-4 py-3.5">
                                  <p className="text-[10px] font-bold tracking-widest uppercase text-emerald-700/50 mb-1.5">
                                    Apply to your content
                                  </p>
                                  <p className="text-[14px] text-foreground/70 font-body leading-[1.7]">
                                    {param.fix || param.whatToChange}
                                  </p>
                                </div>
                              ) : (
                                <div className="mb-4">
                                  <p className="text-[10px] font-bold tracking-widest uppercase text-foreground/35 mb-1.5">
                                    {param.score >= 70 ? 'The refinement' : 'The fix'}
                                  </p>
                                  <p className="text-[14px] text-foreground/65 font-body leading-[1.7]">
                                    {param.fix || param.whatToChange}
                                  </p>
                                </div>
                              )}

                              {/* Step-by-step / Key takeaways checklist */}
                              {steps.length >= 1 && (
                                <div className={`rounded-xl border p-4 mb-4 ${
                                  isCompetitor ? 'border-emerald-200/40 bg-emerald-50/20' : 'border-border bg-secondary/40'
                                }`}>
                                  <div className="flex items-center justify-between mb-3">
                                    <p className={`text-[10px] font-bold tracking-widest uppercase ${
                                      isCompetitor ? 'text-emerald-700/45' : 'text-foreground/40'
                                    }`}>
                                      {isCompetitor ? 'Key takeaways for your content' : 'Step-by-step'}
                                    </p>
                                    <span className="text-[11px] font-body text-foreground/25">
                                      {stepsChecked}/{steps.length} done
                                    </span>
                                  </div>
                                  <div className="flex flex-col gap-2.5">
                                    {steps.map((step, j) => {
                                      const isChecked = checkedSteps.has(`${i}-${j}`)
                                      const stepText = isCaptionParam ? step.replace(/caption above/gi, 'caption below').replace(/suggested caption above/gi, 'suggested caption below') : step
                                      return (
                                        <button
                                          key={j}
                                          onClick={(e) => { e.stopPropagation(); toggleStep(i, j) }}
                                          className="flex gap-3 items-start text-left group cursor-pointer"
                                          style={{ opacity: 0, animation: `fade-slide-in 0.3s ease-out ${j * 80}ms forwards` }}
                                        >
                                          <div className={`w-5 h-5 rounded-md border-2 flex items-center justify-center shrink-0 mt-0.5 transition-all duration-200 ${
                                            isChecked
                                              ? 'bg-emerald-500 border-emerald-500'
                                              : isCompetitor ? 'border-emerald-300/40 bg-background group-hover:border-emerald-400/60' : 'border-foreground/15 bg-background group-hover:border-foreground/25'
                                          }`}>
                                            {isChecked && <Check className="w-3 h-3 text-white" />}
                                          </div>
                                          <p className={`text-[13.5px] font-body leading-[1.7] transition-all duration-200 ${
                                            isChecked ? 'text-foreground/25 line-through' : 'text-foreground/65'
                                          }`}>{stepText}</p>
                                        </button>
                                      )
                                    })}
                                  </div>
                                </div>
                              )}

                              {isCaptionParam && hasTrendingSection && (
                                <button
                                  onClick={() => document.getElementById('trending-section')?.scrollIntoView({ behavior: 'smooth' })}
                                  className="mb-4 w-full text-[14px] font-body font-semibold text-foreground/50 flex items-center justify-center gap-2 cursor-pointer hover:text-foreground/70 transition-colors py-2.5 rounded-xl border border-border/60 bg-secondary/20 hover:bg-secondary/40"
                                >
                                  <span>↓</span> Scroll down to see suggested hashtags and caption
                                </button>
                              )}

                              {/* Why this matters — collapsible */}
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
                                          <p className={`text-[13px] font-body leading-[1.7] ${
                                            isCompetitor ? 'text-foreground/60' : 'text-foreground/55'
                                          }`}>{param.whyItMatters}</p>
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

                  {/* Free user — locked fixes upgrade card */}
                  {isFreeUser && !isCompetitor && weakParams.length > visibleWeakParams.length && (
                    <div className="border border-border rounded-2xl p-5 mt-1 flex flex-col gap-3"
                      style={{ opacity: 0, animation: 'fade-slide-in 0.5s ease-out 200ms forwards' }}>
                      <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-xl border border-border bg-foreground/[0.03] flex items-center justify-center shrink-0">
                          <Lock className="w-4 h-4 text-foreground/25" />
                        </div>
                        <div>
                          <p className="font-display font-bold text-[14px] text-foreground/40">
                            8 parameters are locked
                          </p>
                          <p className="text-[12px] font-body text-foreground/25 mt-0.5">
                            Upgrade to Pro to unlock all 10 step-by-step fixes
                          </p>
                        </div>
                      </div>
                      <a href="/pricing"
                        className="w-full text-center py-2.5 rounded-xl border border-border text-[13px] font-bold font-body text-foreground/50 hover:bg-foreground hover:text-background hover:border-foreground transition-all duration-200">
                        See Pro plans →
                      </a>
                    </div>
                  )}
                </div>
              </div>
              )
            })()}

            {/* ═══ WHAT'S WORKING — shown AFTER fixes for own video ═══ */}
            {!isCompetitor && strongParams.length > 0 && (
              <div className="mb-5">
                <h2 className="font-display text-base font-bold tracking-tight mb-1">{isUpload ? 'Already strong' : "What's Working"}</h2>
                <p className="text-foreground/30 text-[14px] mb-2.5 font-body">Keep doing this</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {strongParams.map((param, i) => (
                    <div key={i} className="flex items-center gap-2.5 px-3.5 py-3 border border-border rounded-xl bg-background"
                      style={{ opacity: 0, animation: `fade-slide-in 0.4s ease-out ${i * 50}ms forwards` }}>
                      <span className="w-1.5 h-1.5 rounded-full bg-green-500/60 shrink-0" />
                      <span className="text-[13px] font-body text-foreground/50 flex-1">{param.name}</span>
                      <span className="font-display font-bold text-[14px] text-green-600/70 shrink-0">{param.score}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* ═══ ADVISORY PARAMETERS ═══ */}
            {!isCompetitor && !isFreeUser && weakAdvisoryParams.length > 0 && (
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

            {/* ═══ YOUR NEXT VIDEO BLUEPRINT — Plus+ only ═══ */}
            {isCompetitor && result.nextVideoTemplate && !canSeeBlueprint && (
              <div className="relative border border-emerald-200/30 rounded-2xl overflow-hidden mb-5"
                style={{ opacity: 0, animation: 'fade-slide-in 0.6s ease-out 150ms forwards' }}>
                <div className="absolute inset-0 bg-background/80 backdrop-blur-sm z-10 flex flex-col items-center justify-center gap-3">
                  <div className="w-12 h-12 rounded-full bg-foreground/[0.06] flex items-center justify-center">
                    <Lock className="w-5 h-5 text-foreground/30" />
                  </div>
                  <p className="text-[14px] font-display font-bold text-foreground/60">Video Blueprint</p>
                  <p className="text-[12px] text-foreground/35 font-body max-w-[30ch] text-center">Upgrade to Pro to get a custom blueprint for your next video based on what worked here</p>
                  <a href="/pricing" className="inline-flex items-center gap-1.5 px-5 py-2 rounded-full bg-foreground text-background text-[12px] font-body font-bold hover:opacity-90 transition-opacity">
                    Upgrade to Pro — ₹799/mo
                  </a>
                </div>
                {/* Blurred preview */}
                <div className="select-none pointer-events-none">
                  <div className="bg-gradient-to-r from-emerald-500/10 via-emerald-400/5 to-transparent px-5 sm:px-6 py-5 border-b border-emerald-200/25">
                    <div className="flex items-center gap-3 mb-1">
                      <div className="w-8 h-8 rounded-lg bg-emerald-100 border border-emerald-200/60 flex items-center justify-center">
                        <svg className="w-4 h-4 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15a2.25 2.25 0 012.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25zM6.75 12h.008v.008H6.75V12zm0 3h.008v.008H6.75V15zm0 3h.008v.008H6.75V18z" />
                        </svg>
                      </div>
                      <div>
                        <h2 className="font-display text-lg font-bold tracking-tight text-foreground/20">Your Next Video Blueprint</h2>
                        <p className="text-[12px] text-foreground/10 font-body">Based on what worked — here's exactly what to create</p>
                      </div>
                    </div>
                  </div>
                  <div className="px-5 py-4 flex flex-col gap-2">
                    {['Hook', 'Structure', 'Audio', 'Location & Setup'].map(label => (
                      <div key={label} className="h-10 rounded-xl bg-foreground/[0.03] border border-border/30" />
                    ))}
                  </div>
                </div>
              </div>
            )}
            {isCompetitor && result.nextVideoTemplate && canSeeBlueprint && (
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

            {/* ═══ TRENDING SECTION ═══ */}
            {hasTrendingSection && !canSeeCaptions && (
              <div className="relative border border-border rounded-2xl p-5 mb-5 overflow-hidden" id="trending-section"
                style={{ opacity: 0, animation: 'fade-slide-in 0.6s ease-out 200ms forwards' }}>
                <div className="absolute inset-0 bg-background/80 backdrop-blur-sm z-10 flex flex-col items-center justify-center gap-3">
                  <div className="w-12 h-12 rounded-full bg-foreground/[0.06] flex items-center justify-center">
                    <Lock className="w-5 h-5 text-foreground/30" />
                  </div>
                  <p className="text-[14px] font-display font-bold text-foreground/60">Captions, hashtags & trending audio</p>
                  <p className="text-[12px] text-foreground/35 font-body max-w-[30ch] text-center">Upgrade to {nextPlan?.name} to unlock AI-generated captions, trending hashtags, and audio suggestions</p>
                  <a href="/pricing" className="inline-flex items-center gap-1.5 px-5 py-2 rounded-full bg-foreground text-background text-[12px] font-body font-bold hover:opacity-90 transition-opacity">
                    Upgrade to {nextPlan?.name} — {nextPlan?.price}
                  </a>
                </div>
                {/* Blurred preview */}
                <div className="select-none pointer-events-none">
                  <h2 className="font-display text-base font-bold tracking-tight mb-0.5 text-foreground/20">Trending for {result.niche}</h2>
                  <p className="text-foreground/10 text-[14px] mb-4 font-body">Real data from {result.platform}</p>
                  <div className="flex flex-wrap gap-1.5 mb-4">
                    {['trending', 'viral', 'reels', 'content', 'creator'].map(t => (
                      <span key={t} className="px-2 py-0.5 rounded-full bg-foreground/[0.03] border border-border/30 text-[14px] font-body text-foreground/10">#{t}</span>
                    ))}
                  </div>
                  <div className="h-16 bg-foreground/[0.02] rounded-xl border border-border/30" />
                </div>
              </div>
            )}
            {hasTrendingSection && canSeeCaptions && (
              <div className="border border-border rounded-2xl p-5 mb-5" id="trending-section"
                style={{ opacity: 0, animation: 'fade-slide-in 0.6s ease-out 200ms forwards' }}>
                <h2 className="font-display text-base font-bold tracking-tight mb-0.5">{isCompetitor ? 'Their Strategy — Use This' : `Trending for ${result.niche}`}</h2>
                <p className="text-foreground/30 text-[14px] mb-4 font-body">{isCompetitor ? 'Hashtags and caption strategy this creator is ranking for — apply these to your content' : `Real data from ${result.platform} — copy and use these`}</p>

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
                      <p className="text-[11px] font-semibold tracking-widest uppercase text-foreground/30">{isCompetitor ? 'Caption for your version' : 'Suggested Caption'}</p>
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

            {/* ═══ UPLOAD: Caption & Hashtags (not scored, ready to use) ═══ */}
            {hasUploadCaptionSection && !canSeeCaptions && (
              <div className="mt-6" id="upload-caption-section">
                <h3 className="font-display text-xl font-bold text-foreground mb-1 tracking-tight">Caption & Hashtags</h3>
                <p className="text-[13px] text-foreground/40 font-body mb-4">Captions and trending hashtags tailored to your content — ready to copy and paste.</p>
                <div className="relative border border-border rounded-2xl p-5 sm:p-6 bg-foreground/[0.015] overflow-hidden">
                  <div className="absolute inset-0 bg-background/80 backdrop-blur-sm z-10 flex flex-col items-center justify-center gap-3">
                    <div className="w-12 h-12 rounded-full bg-foreground/[0.06] flex items-center justify-center">
                      <Lock className="w-5 h-5 text-foreground/30" />
                    </div>
                    <p className="text-[14px] font-display font-bold text-foreground/60">Unlock captions & hashtags</p>
                    <p className="text-[12px] text-foreground/35 font-body max-w-[30ch] text-center">Upgrade to {nextPlan?.name} to get AI-generated captions, hashtags, and trending audio for every video</p>
                    <a href="/pricing" className="inline-flex items-center gap-1.5 px-5 py-2 rounded-full bg-foreground text-background text-[12px] font-body font-bold hover:opacity-90 transition-opacity">
                      Upgrade to {nextPlan?.name} — {nextPlan?.price}
                    </a>
                  </div>
                  <div className="select-none pointer-events-none">
                    <div className="flex flex-wrap gap-1.5 mb-4">
                      {['trending', 'viral', 'reels', 'fyp', 'explore'].map(t => (
                        <span key={t} className="px-2.5 py-1 rounded-full bg-foreground/[0.03] border border-border/30 text-xs font-body text-foreground/10">#{t}</span>
                      ))}
                    </div>
                    <div className="h-20 bg-foreground/[0.02] rounded-xl border border-border/30 mb-4" />
                    <div className="h-8 bg-foreground/[0.02] rounded-lg border border-border/30 w-2/3" />
                  </div>
                </div>
              </div>
            )}
            {hasUploadCaptionSection && canSeeCaptions && (
              <div className="mt-6" id="upload-caption-section">
                <h3 className="font-display text-xl font-bold text-foreground mb-1 tracking-tight">Caption & Hashtags</h3>
                <p className="text-[13px] text-foreground/40 font-body mb-4">Captions and trending hashtags tailored to your content — ready to copy and paste.</p>
                <div className="border border-border rounded-2xl p-5 sm:p-6 bg-foreground/[0.015]">
                  {result.suggestedHashtags?.length > 0 && (
                    <div className="mb-5">
                      <div className="flex items-center justify-between mb-2">
                        <p className="text-[11px] font-semibold tracking-widest uppercase text-foreground/30">Suggested Hashtags</p>
                        <button onClick={() => copyText(result.suggestedHashtags.filter((h: string) => h.replace(/#/g, '').trim().length > 0).map((h: string) => h.startsWith('#') ? h : `#${h}`).join(' '), 'upload-hashtags')}
                          className="text-[14px] text-foreground/30 hover:text-foreground/50 cursor-pointer transition-colors font-body underline">
                          {copied === 'upload-hashtags' ? 'Copied!' : 'Copy all'}
                        </button>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {result.suggestedHashtags
                          .map((tag: string) => tag.replace(/^#+/, '').trim())
                          .filter((tag: string) => tag.length > 0)
                          .map((tag: string, i: number) => (
                          <span key={i} className="px-2.5 py-1 rounded-full bg-secondary/60 border border-border text-xs font-body text-foreground/50">
                            #{tag}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {result.suggestedCaption && (
                    <div className="mb-5">
                      <div className="flex items-center justify-between mb-2">
                        <p className="text-[11px] font-semibold tracking-widest uppercase text-foreground/30">Suggested Caption</p>
                        <button onClick={() => copyText(result.suggestedCaption, 'upload-caption')}
                          className="text-[14px] text-foreground/30 hover:text-foreground/50 cursor-pointer transition-colors font-body underline">
                          {copied === 'upload-caption' ? 'Copied!' : 'Copy'}
                        </button>
                      </div>
                      <div className="bg-secondary/30 border border-border rounded-xl p-3.5">
                        <p className="text-[14px] text-foreground/60 font-body leading-relaxed whitespace-pre-wrap">{result.suggestedCaption}</p>
                      </div>
                    </div>
                  )}

                  {result.trendingAudioSuggestion && (
                    <div>
                      <p className="text-[11px] font-semibold tracking-widest uppercase text-foreground/30 mb-2">Trending Audio Suggestion</p>
                      <p className="text-[13px] text-foreground/50 font-body leading-relaxed">{result.trendingAudioSuggestion}</p>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* ═══ BOTTOM ACTIONS ═══ */}
            <div className="mt-8 mb-4">
              <a href="/analyse"
                className="block w-full text-center py-3.5 rounded-xl border border-border text-foreground/50 text-[14px] font-body font-medium hover:bg-secondary/50 hover:border-foreground/15 transition-all">
                Analyse another video
              </a>
            </div>

          </div>
        </div>
      </div>

      {/* ═══ FLOATING CHAT BUTTON ═══ */}
      <style>{`
        .chat-fab { width: 48px; }
        .chat-fab:hover { width: 210px; }
        .chat-fab .chat-label { max-width: 0; opacity: 0; overflow: hidden; }
        .chat-fab:hover .chat-label { max-width: 160px; opacity: 1; }
      `}</style>
      <button
        onClick={() => navigate('/chat')}
        className="chat-fab fixed bottom-6 right-6 z-50 h-12 rounded-full bg-foreground text-background flex items-center shadow-lg hover:shadow-xl overflow-hidden cursor-pointer"
        style={{ animation: 'fade-slide-in 0.5s ease-out 1s forwards', opacity: 0, transition: 'width 0.3s ease, box-shadow 0.3s ease' }}>
        <div className="w-12 h-12 flex items-center justify-center shrink-0">
          <svg className="w-5 h-5 fill-current" viewBox="0 0 24 24">
            <path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456z" />
          </svg>
        </div>
        <span className="chat-label whitespace-nowrap text-[13px] font-display font-semibold pr-5" style={{ transition: 'max-width 0.3s ease, opacity 0.3s ease 0.1s' }}>
          Chat with ARIA
        </span>
      </button>
    </div>
  )
}
