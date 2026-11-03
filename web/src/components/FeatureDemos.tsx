import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useScrollAnimate } from '@/hooks/useScrollAnimate'
import {
  ArrowRight, BarChart3, Zap, MessageSquare, Hash,
  Sparkles, Search, Lightbulb, Target, TrendingUp,
  CheckCircle, Eye, Clock, Layers, PenTool, Type,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

interface FeatureSection {
  id: string
  pillLabel: string
  tag: string
  headline: string
  description: string
  bullets: { icon: LucideIcon; text: string }[]
}

const features: FeatureSection[] = [
  {
    id: 'score',
    pillLabel: '10-Parameter Score',
    tag: 'Score',
    headline: '10 parameters.\nOne score.',
    description: 'Every video gets a score from 0 to 100 based on 10 real ranking signals. Here\'s what each range means — and what it takes to move up.',
    bullets: [
      { icon: BarChart3, text: 'Weighted scoring across hook, pacing, audio, visuals, and 6 more parameters' },
      { icon: Target, text: 'Clear benchmarks so you know exactly where your content stands' },
      { icon: TrendingUp, text: 'Every fix tells you the expected score improvement' },
    ],
  },
  {
    id: 'fixes',
    pillLabel: 'Step-by-Step Fixes',
    tag: 'Fixes',
    headline: 'Not just what\'s wrong —\nhow to fix it.',
    description: 'Every parameter with a low score gets a detailed breakdown — what\'s wrong, why it matters, and a step-by-step checklist you can follow in your editing app.',
    bullets: [
      { icon: Zap, text: 'Each fix shows the problem, the reason, and exact steps to follow' },
      { icon: CheckCircle, text: 'Interactive checklist — tick off steps as you apply them' },
      { icon: Clock, text: 'Fixes ranked by impact so you know what to change first' },
    ],
  },
  {
    id: 'caption-studio',
    pillLabel: 'Caption Studio',
    tag: 'Caption Studio',
    headline: 'Captions and hashtags,\nready to post.',
    description: 'AI-generated captions that hook viewers in the first line, paired with trending, niche-specific hashtags that actually get your content discovered.',
    bullets: [
      { icon: PenTool, text: 'Captions matched to your tone — multiple variations to choose from' },
      { icon: Hash, text: 'Trending hashtags grouped by reach, community, and discovery' },
      { icon: Eye, text: 'Optimised for mute viewers, search, and discoverability' },
    ],
  },
  {
    id: 'competitors',
    pillLabel: 'Competitor Analysis',
    tag: 'Competitors',
    headline: 'Decode any viral reel\nin your niche.',
    description: 'Paste the Instagram link of any creator\'s reel — Prysm breaks down exactly what made it work. Same 10 parameters, but framed as techniques you can learn and apply to your own content.',
    bullets: [
      { icon: Sparkles, text: 'Paste any public Instagram reel link to analyze it' },
      { icon: Eye, text: 'See what techniques drove their virality — hook, pacing, audio, CTA' },
      { icon: Target, text: 'Get actionable takeaways you can apply to your next reel' },
    ],
  },
  {
    id: 'past-posts',
    pillLabel: 'Analyze Past Posts',
    tag: 'Past Posts',
    headline: 'Understand why your\npast reels performed.',
    description: 'Paste the link to any reel from your own profile — Prysm tells you exactly what worked, what didn\'t, and what to do differently next time. Learn from your own content history.',
    bullets: [
      { icon: Search, text: 'Paste any of your published reel links to analyze' },
      { icon: Eye, text: 'Understand why a reel went viral or flopped — parameter by parameter' },
      { icon: TrendingUp, text: 'See what to repeat and what to change in your next video' },
    ],
  },
  {
    id: 'aria',
    pillLabel: 'Ask ARIA',
    tag: 'ARIA',
    headline: 'Your AI creative\ncoach, on demand.',
    description: 'Got a question about your score? Want to brainstorm hooks? ARIA is your always-on creative partner — trained on what makes short-form content perform.',
    bullets: [
      { icon: MessageSquare, text: 'Ask follow-up questions about any part of your analysis' },
      { icon: Lightbulb, text: 'Brainstorm hooks, CTAs, and content ideas for your niche' },
      { icon: Sparkles, text: 'Get personalised advice based on your creator profile' },
    ],
  },
  {
    id: 'insights',
    pillLabel: 'Deep Insights',
    tag: 'Insights',
    headline: 'Know exactly why\neach score matters.',
    description: 'Every parameter comes with a "Why this matters" section backed by real algorithm data — so you understand the reasoning behind each fix, not just what to change.',
    bullets: [
      { icon: Lightbulb, text: 'Algorithm-backed reasoning for every score you receive' },
      { icon: TrendingUp, text: 'Expected improvement stats based on real creator data' },
      { icon: Layers, text: 'Understand the "why" so you can apply it to every future reel' },
    ],
  },
]

/* ── Text content (no scroll animation — uses tab fade) ── */
const TextContent = ({ feature }: { feature: FeatureSection }) => (
  <div>
    <span className="tag-chip inline-flex mb-4">{feature.tag}</span>
    <h3 className="font-display text-xl sm:text-2xl md:text-[2.5rem] font-bold text-foreground tracking-tight leading-[1.1] mb-3 sm:mb-4 whitespace-pre-line">
      {feature.headline}
    </h3>
    <p className="text-foreground/50 font-body text-[0.875rem] sm:text-base leading-relaxed max-w-[50ch] mb-4 sm:mb-6">
      {feature.description}
    </p>
    <div className="space-y-2.5 sm:space-y-3 mb-6 sm:mb-8">
      {feature.bullets.map((b, j) => (
        <div key={j} className="flex items-start gap-2.5 sm:gap-3">
          <div className="w-7 h-7 sm:w-8 sm:h-8 rounded-lg bg-secondary/60 border border-border/60 flex items-center justify-center flex-shrink-0 mt-0.5">
            <b.icon className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-foreground/40" />
          </div>
          <span className="text-[12px] sm:text-[14px] font-body text-foreground/60 leading-relaxed pt-0.5 sm:pt-1">
            {b.text}
          </span>
        </div>
      ))}
    </div>
    <a href="/register"
      className="inline-flex items-center gap-2 text-[13px] font-body font-semibold text-foreground/60 hover:text-foreground transition-colors duration-200">
      Try it free <ArrowRight className="w-3.5 h-3.5" />
    </a>
  </div>
)

/* ── Score tiers mockup ── */
const scoreTiers = [
  { range: '86–100', label: 'Viral Ready', color: '#16a34a', bg: 'rgba(22,163,74,0.08)', border: 'rgba(22,163,74,0.18)', barWidth: '100%', desc: 'Optimised across all parameters — ready to perform' },
  { range: '71–85', label: 'Strong', color: '#22c55e', bg: 'rgba(34,197,94,0.07)', border: 'rgba(34,197,94,0.15)', barWidth: '82%', desc: 'Solid execution — a few tweaks away from viral' },
  { range: '51–70', label: 'Good Potential', color: '#eab308', bg: 'rgba(234,179,8,0.07)', border: 'rgba(234,179,8,0.15)', barWidth: '62%', desc: 'Good foundation — apply the fixes to level up' },
  { range: '31–50', label: 'Below Average', color: '#f97316', bg: 'rgba(249,115,22,0.07)', border: 'rgba(249,115,22,0.15)', barWidth: '42%', desc: 'Multiple weak areas — focus on top priorities first' },
  { range: '0–30', label: 'Needs Work', color: '#ef4444', bg: 'rgba(239,68,68,0.07)', border: 'rgba(239,68,68,0.15)', barWidth: '22%', desc: 'Significant issues across most parameters' },
]

const ScoreMockup = () => (
  <div className="rounded-2xl border border-border bg-background p-5 sm:p-6 max-w-[340px] mx-auto"
    style={{ boxShadow: '0 4px 32px hsl(44 35% 55% / 0.08)' }}>
    <div className="flex items-center gap-2.5 mb-5">
      <BarChart3 className="w-4 h-4 text-foreground/30" />
      <span className="font-display text-[14px] font-bold text-foreground">Score Breakdown</span>
    </div>
    <div className="flex flex-col gap-2.5">
      {scoreTiers.map((tier, i) => (
        <div key={tier.range}
          className="rounded-xl px-3.5 py-3 border"
          style={{
            background: tier.bg,
            borderColor: tier.border,
            opacity: 0,
            animation: `fade-slide-in 0.35s ease-out ${700 + i * 100}ms forwards`,
          }}>
          <div className="flex items-center justify-between mb-1.5">
            <div className="flex items-center gap-2">
              <span className="font-display text-[13px] font-bold" style={{ color: tier.color }}>{tier.range}</span>
              <span className="text-[10px] font-bold font-display tracking-wide uppercase px-1.5 py-0.5 rounded"
                style={{ color: tier.color, background: tier.bg }}>
                {tier.label}
              </span>
            </div>
          </div>
          <div className="w-full h-1.5 rounded-full bg-foreground/[0.04] mb-1.5">
            <div className="h-full rounded-full transition-all duration-700"
              style={{ width: tier.barWidth, background: tier.color, opacity: 0.5 }} />
          </div>
          <p className="text-[10px] font-body text-foreground/40 leading-relaxed">{tier.desc}</p>
        </div>
      ))}
    </div>
  </div>
)

/* ── Fixes mockup ── */
const FixesMockup = () => (
  <div className="rounded-2xl border border-border bg-background overflow-hidden"
    style={{ boxShadow: '0 4px 32px hsl(44 35% 55% / 0.08)' }}>

    {/* Gradient header — like actual analysis */}
    <div className="px-4 py-3.5 flex items-center gap-3 bg-gradient-to-r from-red-500/10 to-red-500/[0.02] border-b border-border/40">
      <div className="w-10 h-10 rounded-xl border bg-red-50 border-red-200/50 flex items-center justify-center flex-shrink-0">
        <Zap className="w-[18px] h-[18px] text-red-500 opacity-70" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-display font-bold text-[13px] text-foreground">Hook & Opening</span>
          <span className="text-[8px] font-bold tracking-wider uppercase px-1.5 py-0.5 rounded border bg-red-100 text-red-700 border-red-200">CRITICAL</span>
          <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
        </div>
      </div>
      <span className="font-display font-bold text-base text-red-500">34</span>
    </div>

    {/* Expanded content */}
    <div className="px-4 pt-3 pb-4">
      {/* Problem */}
      <div className="mb-3">
        <p className="text-[9px] font-semibold tracking-widest uppercase text-foreground/25 mb-1">The problem</p>
        <p className="text-[12px] font-body leading-relaxed text-red-600/55">
          The first 3 seconds have no speech, no text, and no face — mute viewers see a dark wide shot with zero context and scroll past immediately.
        </p>
      </div>

      {/* Fix */}
      <div className="mb-3">
        <p className="text-[9px] font-semibold tracking-widest uppercase text-foreground/25 mb-1">The fix</p>
        <p className="text-[12px] text-foreground/55 font-body leading-relaxed">
          Add a bold text overlay at 0.0s that creates instant curiosity — give viewers a reason to stop scrolling within 1.7 seconds.
        </p>
      </div>

      {/* Step-by-step checklist */}
      <div className="rounded-xl border border-border/60 bg-secondary/30 p-3.5">
        <div className="flex items-center justify-between mb-2.5">
          <p className="text-[9px] font-semibold tracking-widest uppercase text-foreground/30">Step-by-step</p>
          <span className="text-[9px] font-body text-foreground/20">0/4 done</span>
        </div>
        <div className="flex flex-col gap-2">
          {[
            'Open your editing app and add a text layer at 0.0s',
            'Write a curiosity-driven hook — a question or bold claim',
            'Set duration to 2 seconds so it appears immediately',
            'Use a bold font with a contrasting outline for readability',
          ].map((step, j) => (
            <div key={j} className="flex gap-2.5 items-start"
              style={{ opacity: 0, animation: `fade-slide-in 0.3s ease-out ${900 + j * 100}ms forwards` }}>
              <div className="w-5 h-5 rounded-md border border-border bg-background flex items-center justify-center shrink-0 mt-0.5" />
              <p className="text-[11px] font-body text-foreground/55 leading-relaxed">{step}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  </div>
)

/* ── Caption Studio mockup ── */
const CaptionMockup = () => (
  <div className="rounded-2xl border border-border bg-background p-5 sm:p-6"
    style={{ boxShadow: '0 4px 32px hsl(44 35% 55% / 0.08)' }}>
    <div className="flex items-center justify-between mb-4">
      <span className="font-display text-[14px] font-bold text-foreground">Caption & Hashtags</span>
      <span className="text-[10px] font-bold tracking-wider uppercase px-2 py-0.5 rounded-full bg-green-50/60 border border-green-200/40 text-green-600/70 font-body">Generated</span>
    </div>
    <div className="rounded-xl border border-border p-3.5 mb-3">
      <span className="text-[9px] font-bold uppercase tracking-widest font-body text-foreground/30">Suggested Caption</span>
      <p className="text-[11.5px] font-body leading-relaxed text-foreground/60 mt-1.5">
        "Wait for it... this one change made all the difference. Save this for your next edit."
      </p>
    </div>
    <div className="rounded-xl border border-border p-3.5">
      <span className="text-[9px] font-bold uppercase tracking-widest font-body text-foreground/30 mb-2 block">Trending Hashtags</span>
      <div className="flex flex-wrap gap-1.5">
        {['#reelsinstagram', '#viralreels', '#contentcreator', '#growthhacks', '#reelsviral', '#creatoreconomy'].map(tag => (
          <span key={tag} className="px-2 py-1 rounded-md bg-secondary/50 border border-border/60 text-[10px] font-body text-foreground/50">{tag}</span>
        ))}
      </div>
    </div>
  </div>
)

/* ── Competitor mockup ── */
const CompetitorMockup = () => (
  <div className="rounded-2xl border border-border bg-background overflow-hidden"
    style={{ boxShadow: '0 4px 32px hsl(44 35% 55% / 0.08)' }}>
    {/* URL input bar */}
    <div className="px-4 py-3 border-b border-border/40 bg-secondary/20">
      <div className="flex items-center gap-2 rounded-lg border border-border bg-background px-3 py-2">
        <Search className="w-3.5 h-3.5 text-foreground/25 flex-shrink-0" />
        <span className="text-[11px] font-body text-foreground/30 truncate">instagram.com/reel/C8xK2mNpQr5/</span>
      </div>
    </div>
    {/* Analysis result preview */}
    <div className="p-4">
      <div className="flex items-center gap-3 mb-4">
        <div className="relative w-14 h-14 flex-shrink-0">
          <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90">
            <circle cx="50" cy="50" r="40" fill="none" stroke="hsl(44 42% 90%)" strokeWidth="8" />
            <circle cx="50" cy="50" r="40" fill="none" stroke="hsl(142 55% 40%)" strokeWidth="8"
              strokeLinecap="round" strokeDasharray="251" strokeDashoffset="44"
              className="draw-ring-anim" style={{ '--offset': '44' } as React.CSSProperties} />
          </svg>
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="font-display text-lg font-black text-foreground">83</span>
          </div>
        </div>
        <div className="flex-1 min-w-0">
          <span className="text-[8px] font-bold tracking-widest uppercase px-1.5 py-0.5 rounded-full bg-green-50 text-green-700 border border-green-200/40">Excellent Execution</span>
          <p className="text-[11px] text-foreground/45 font-body mt-1 leading-relaxed">Their opening face-to-camera hook creates instant curiosity — apply this to your next reel.</p>
        </div>
      </div>
      {/* Techniques */}
      <div className="space-y-2">
        {[
          { name: 'Hook & Opening', score: 88, label: 'STRONG', color: 'text-green-600/70 bg-green-50/50 border-green-200/40' },
          { name: 'Shareability', score: 82, label: 'STRONG', color: 'text-green-600/70 bg-green-50/50 border-green-200/40' },
          { name: 'On-Screen Text', score: 45, label: 'LEARN', color: 'text-amber-600/70 bg-amber-50/50 border-amber-200/40' },
        ].map((p, i) => (
          <div key={i} className="flex items-center gap-2 px-3 py-2 rounded-lg border border-border/40"
            style={{ opacity: 0, animation: `fade-slide-in 0.3s ease-out ${900 + i * 100}ms forwards` }}>
            <span className="text-[10px] font-body text-foreground/50 flex-1">{p.name}</span>
            <span className={`font-display font-bold text-[12px] ${p.score >= 70 ? 'text-green-600/70' : 'text-amber-600/70'}`}>{p.score}</span>
            <span className={`text-[7px] font-bold tracking-wider uppercase px-1.5 py-0.5 rounded border ${p.color}`}>{p.label}</span>
          </div>
        ))}
      </div>
      <p className="text-[10px] text-foreground/25 font-body mt-3 text-center">+ 7 more parameters analyzed</p>
    </div>
  </div>
)

/* ── Past Posts mockup ── */
const PastPostsMockup = () => (
  <div className="rounded-2xl border border-border bg-background overflow-hidden"
    style={{ boxShadow: '0 4px 32px hsl(44 35% 55% / 0.08)' }}>
    {/* URL input bar */}
    <div className="px-4 py-3 border-b border-border/40 bg-secondary/20">
      <div className="flex items-center gap-2 rounded-lg border border-border bg-background px-3 py-2">
        <Search className="w-3.5 h-3.5 text-foreground/25 flex-shrink-0" />
        <span className="text-[11px] font-body text-foreground/30 truncate">instagram.com/reel/your-post-link/</span>
      </div>
    </div>
    {/* Analysis result preview */}
    <div className="p-4">
      <div className="flex items-center gap-3 mb-4">
        <div className="relative w-14 h-14 flex-shrink-0">
          <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90">
            <circle cx="50" cy="50" r="40" fill="none" stroke="hsl(44 42% 90%)" strokeWidth="8" />
            <circle cx="50" cy="50" r="40" fill="none" stroke="hsl(38 90% 50%)" strokeWidth="8"
              strokeLinecap="round" strokeDasharray="251" strokeDashoffset="88"
              className="draw-ring-anim" style={{ '--offset': '88' } as React.CSSProperties} />
          </svg>
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="font-display text-lg font-black text-foreground">65</span>
          </div>
        </div>
        <div className="flex-1 min-w-0">
          <span className="text-[8px] font-bold tracking-widest uppercase px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200/40">Good Potential, Needs Polish</span>
          <p className="text-[11px] text-foreground/45 font-body mt-1 leading-relaxed">Your hook was strong but no CTA meant viewers left without following — fix this next time.</p>
        </div>
      </div>
      {/* Key learnings */}
      <div className="rounded-xl border border-border/50 bg-secondary/20 p-3 mb-3">
        <p className="text-[9px] font-semibold tracking-widest uppercase text-foreground/25 mb-2">What worked</p>
        <div className="space-y-1.5">
          {['Hook & Opening — 81', 'Audio & Music — 76'].map((item, i) => (
            <div key={i} className="flex items-center gap-2"
              style={{ opacity: 0, animation: `fade-slide-in 0.3s ease-out ${900 + i * 80}ms forwards` }}>
              <span className="w-1.5 h-1.5 rounded-full bg-green-500/60 flex-shrink-0" />
              <span className="text-[11px] font-body text-foreground/50">{item}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="rounded-xl border border-border/50 bg-secondary/20 p-3">
        <p className="text-[9px] font-semibold tracking-widest uppercase text-foreground/25 mb-2">What to improve next time</p>
        <div className="space-y-1.5">
          {['Call to Action — 28', 'Save Potential — 35'].map((item, i) => (
            <div key={i} className="flex items-center gap-2"
              style={{ opacity: 0, animation: `fade-slide-in 0.3s ease-out ${1100 + i * 80}ms forwards` }}>
              <span className="w-1.5 h-1.5 rounded-full bg-red-400/60 flex-shrink-0" />
              <span className="text-[11px] font-body text-foreground/50">{item}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  </div>
)

/* ── ARIA chat mockup ── */
const AriaMockup = () => (
  <div className="rounded-2xl border border-border bg-background p-5 sm:p-6"
    style={{ boxShadow: '0 4px 32px hsl(44 35% 55% / 0.08)' }}>
    <div className="flex items-center gap-2 mb-4">
      <div className="w-6 h-6 rounded-full bg-foreground flex items-center justify-center">
        <Sparkles className="w-3 h-3 text-background" />
      </div>
      <span className="font-display text-[14px] font-bold text-foreground">ARIA</span>
      <span className="text-[10px] font-body text-foreground/30 ml-auto">AI Creative Coach</span>
    </div>
    <div className="space-y-3">
      {/* User message */}
      <div className="flex justify-end">
        <div className="bg-foreground/[0.05] rounded-2xl rounded-br-md px-3.5 py-2.5 max-w-[80%]">
          <p className="text-[11px] font-body text-foreground/70 leading-relaxed">Why did my hook score so low?</p>
        </div>
      </div>
      {/* ARIA response */}
      <div className="flex justify-start">
        <div className="bg-secondary/50 border border-border/60 rounded-2xl rounded-bl-md px-3.5 py-2.5 max-w-[85%]">
          <p className="text-[11px] font-body text-foreground/60 leading-relaxed">
            Your first 2 seconds are a wide landscape shot with no text or speech. Viewers scrolling on mute have zero context. Try opening with a bold text question — that alone could push your hook score from 42 to 65+.
          </p>
        </div>
      </div>
      {/* User follow-up */}
      <div className="flex justify-end">
        <div className="bg-foreground/[0.05] rounded-2xl rounded-br-md px-3.5 py-2.5 max-w-[80%]">
          <p className="text-[11px] font-body text-foreground/70 leading-relaxed">What text should I use?</p>
        </div>
      </div>
      {/* ARIA typing */}
      <div className="flex justify-start">
        <div className="bg-secondary/50 border border-border/60 rounded-2xl rounded-bl-md px-3.5 py-2.5">
          <div className="flex items-center gap-1">
            <div className="w-1.5 h-1.5 rounded-full bg-foreground/30" style={{ animation: 'typing-dot 1.4s ease-in-out infinite 0ms' }} />
            <div className="w-1.5 h-1.5 rounded-full bg-foreground/30" style={{ animation: 'typing-dot 1.4s ease-in-out infinite 200ms' }} />
            <div className="w-1.5 h-1.5 rounded-full bg-foreground/30" style={{ animation: 'typing-dot 1.4s ease-in-out infinite 400ms' }} />
          </div>
        </div>
      </div>
    </div>
  </div>
)

/* ── Deep Insights mockup ── */
const InsightsMockup = () => (
  <div className="rounded-2xl border border-border bg-background p-5 sm:p-6"
    style={{ boxShadow: '0 4px 32px hsl(44 35% 55% / 0.08)' }}>
    {/* Why this matters card — matching the actual analysis UI */}
    <div className="rounded-xl border border-border/50 bg-secondary/20 p-4">
      <div className="flex items-start gap-2.5">
        <div className="w-7 h-7 rounded-lg bg-amber-100/50 border border-amber-200/40 flex items-center justify-center flex-shrink-0 mt-0.5">
          <Lightbulb className="w-3.5 h-3.5 text-amber-600/60" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-[9px] font-semibold tracking-widest uppercase text-foreground/30 mb-1.5">Why this matters</p>
          <p className="text-[12px] text-foreground/40 font-body leading-relaxed"
            style={{ opacity: 0, animation: 'fade-slide-in 0.4s ease-out 800ms forwards' }}>
            Watch time is Instagram's confirmed #1 ranking signal. Even one dead zone where nothing is visually happening can cause a retention cliff — viewers leave and the algorithm reads this as low-quality content.
          </p>
          <div className="mt-3 flex items-start gap-2 rounded-lg bg-green-50/40 border border-green-200/30 px-3 py-2"
            style={{ opacity: 0, animation: 'fade-slide-in 0.4s ease-out 1000ms forwards' }}>
            <TrendingUp className="w-3.5 h-3.5 text-green-600/50 flex-shrink-0 mt-0.5" />
            <p className="text-[11px] font-body leading-relaxed text-green-700/50">
              <span className="font-semibold text-green-700/65">Expected: </span>
              Removing these two dead zones should improve your mid-video retention by roughly 10-20%, which means more viewers reaching the punchline and the ending — the two moments most likely to drive shares.
            </p>
          </div>
        </div>
      </div>
    </div>
  </div>
)

/* ── Mockup map ── */
const mockupComponents: Record<string, React.FC> = {
  score: ScoreMockup,
  fixes: FixesMockup,
  'caption-studio': CaptionMockup,
  competitors: CompetitorMockup,
  'past-posts': PastPostsMockup,
  aria: AriaMockup,
  insights: InsightsMockup,
}

/* ── Mockup panel wrapper ── */
const MockupPanel = ({ feature }: { feature: FeatureSection }) => {
  const Mockup = mockupComponents[feature.id]
  return Mockup ? <Mockup /> : (
    <div className="w-full aspect-[4/3] rounded-2xl border border-border bg-secondary/20 flex items-center justify-center">
      <span className="text-foreground/15 font-body text-sm tracking-wide">{feature.tag} mockup</span>
    </div>
  )
}

/* ── Main component ── */
const FeatureDemos = () => {
  const [activeIndex, setActiveIndex] = useState(0)
  const titleRef = useScrollAnimate<HTMLDivElement>()
  const sectionRef = useRef<HTMLElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  // IntersectionObserver to track which card is in view
  useEffect(() => {
    const container = scrollRef.current
    if (!container) return
    const cards = container.querySelectorAll<HTMLElement>('[data-card-index]')

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            const idx = parseInt(entry.target.getAttribute('data-card-index') || '0', 10)
            setActiveIndex(idx)
            window.dispatchEvent(new CustomEvent('prysm:active-pill', { detail: { label: features[idx].pillLabel } }))
          }
        }
      },
      { root: container, threshold: 0.55 }
    )

    cards.forEach(c => observer.observe(c))
    return () => observer.disconnect()
  }, [])

  // Scroll to a specific card when dot is clicked or hero pill triggers it
  const scrollToCard = useCallback((index: number) => {
    const container = scrollRef.current
    if (!container) return
    const card = container.children[index] as HTMLElement
    if (card) card.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' })
  }, [])

  // Listen for hero pill scroll-to-card events
  useEffect(() => {
    const handler = (e: Event) => {
      const index = (e as CustomEvent).detail?.index
      if (typeof index === 'number') scrollToCard(index)
    }
    window.addEventListener('prysm:scroll-to-card', handler)
    return () => window.removeEventListener('prysm:scroll-to-card', handler)
  }, [scrollToCard])

  return (
    <section id="product" ref={sectionRef} className="border-t border-border">
      <div className="max-w-6xl mx-auto py-16 sm:py-24">

        {/* Section header */}
        <div ref={titleRef} className="scroll-animate mb-10 sm:mb-14 px-4 sm:px-6">
          <span className="tag-chip mb-5 inline-flex">Product</span>
          <h2 className="font-display text-2xl sm:text-4xl md:text-5xl font-bold text-foreground tracking-tight leading-[1.1]">
            Everything you need,
            <br />
            <span className="text-foreground/30">before you post</span>
          </h2>
          <p className="mt-4 text-foreground/50 font-body prose-width leading-relaxed">
            A complete AI toolkit that helps you understand, improve, and grow every piece of content you create.
          </p>
        </div>

        {/* Progress dots + label */}
        <div className="flex items-center justify-between mb-5 px-4 sm:px-6">
          <div className="flex gap-1.5">
            {features.map((_, i) => (
              <button key={i} onClick={() => scrollToCard(i)}
                className={`h-1.5 rounded-full transition-all duration-300 ${
                  activeIndex === i ? 'w-8 bg-foreground/50' : 'w-1.5 bg-foreground/12 hover:bg-foreground/20'
                }`} />
            ))}
          </div>
          <span className="text-[12px] font-body text-foreground/30 tabular-nums">
            {activeIndex + 1} / {features.length} — {features[activeIndex].pillLabel}
          </span>
        </div>

        {/* Horizontal scroll snap container */}
        <div
          ref={scrollRef}
          className="flex gap-4 sm:gap-6 overflow-x-auto snap-x snap-mandatory scrollbar-hide px-4 sm:px-6"
          style={{ scrollPaddingInline: '1rem' }}
        >
          {features.map((feature, i) => (
            <div
              key={feature.id}
              id={`feat-${feature.id}`}
              data-card-index={i}
              className="snap-center flex-shrink-0 w-[88vw] sm:w-[82vw] md:w-[75vw] lg:w-[70vw] max-w-5xl"
            >
              <div className="rounded-2xl border border-border/40 bg-background p-4 sm:p-6 md:p-10 h-full"
                style={{ boxShadow: '0 2px 24px hsl(44 35% 55% / 0.06)' }}>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-8 lg:gap-14 items-center">
                  <div>
                    <TextContent feature={feature} />
                  </div>
                  <div className="hidden sm:block">
                    <MockupPanel feature={feature} />
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Swipe hint */}
        <div className="flex justify-center mt-5">
          <span className="text-[11px] font-body text-foreground/20">Swipe to explore all features →</span>
        </div>
      </div>
    </section>
  )
}
export default FeatureDemos
