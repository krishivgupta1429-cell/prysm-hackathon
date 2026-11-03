import { useState, useEffect } from 'react'
import { STUDIO_MODE } from '@/lib/config'
import { visibleStages, doneCount, stageState, type Progress } from '@/studio/progress'

interface Props {
  status: string
  /** The backend's real stage progress (demo mode); the screen follows it instead of simulated timers. */
  progress?: Progress | null
}

const FACTS = [
  'Prysm analyses 10 parameters based on real ranking signals',
  'The first 2 seconds of your video decide 80% of its reach',
  'Videos with on-screen text get 40% more engagement',
  'A strong CTA can double your save rate',
  'Trending audio boosts discoverability by 3x',
  'Consistent posting beats viral moments for growth',
  'Reels under 15 seconds have the highest completion rate',
]

export default function PrismProcessing({ status, progress }: Props) {
  const [elapsed, setElapsed] = useState(0)
  const [factIdx, setFactIdx] = useState(0)

  useEffect(() => {
    const timer = setInterval(() => setElapsed(e => e + 1), 1000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    const timer = setInterval(() => setFactIdx(i => (i + 1) % FACTS.length), 6000)
    return () => clearInterval(timer)
  }, [])

  const minutes = Math.floor(elapsed / 60)
  const seconds = elapsed % 60

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col items-center justify-center px-6 relative overflow-hidden">

      {/* Background ambient rings */}
      <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
        {[1, 2, 3].map(i => (
          <div key={i} className="absolute rounded-full border border-foreground/[0.03]"
            style={{
              width: `${200 + i * 160}px`,
              height: `${200 + i * 160}px`,
              animation: `pulse-ring ${4 + i * 2}s ease-in-out infinite`,
              animationDelay: `${i * 0.8}s`,
            }}
          />
        ))}
      </div>

      {/* Main animation container */}
      <div className="relative mb-16" style={{ width: 200, height: 200 }}>

        {/* Outer rotating ring */}
        <svg className="absolute inset-0 w-full h-full" viewBox="0 0 200 200" style={{ animation: 'spin-slow 12s linear infinite' }}>
          <circle cx="100" cy="100" r="90" fill="none" stroke="currentColor" strokeWidth="0.5" className="text-foreground/[0.06]" />
          <circle cx="100" cy="100" r="90" fill="none" strokeWidth="1.5" strokeLinecap="round"
            className="text-foreground/20"
            strokeDasharray="40 520"
          />
        </svg>

        {/* Middle counter-rotating ring */}
        <svg className="absolute inset-0 w-full h-full" viewBox="0 0 200 200" style={{ animation: 'spin-reverse 8s linear infinite' }}>
          <circle cx="100" cy="100" r="70" fill="none" strokeWidth="1" strokeLinecap="round"
            className="text-foreground/10"
            strokeDasharray="20 420"
          />
        </svg>

        {/* Central prism — breathing geometric shape */}
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="relative" style={{ animation: 'breathe 4s ease-in-out infinite' }}>
            {/* Triangle */}
            <svg width="80" height="80" viewBox="0 0 80 80" className="drop-shadow-sm">
              <polygon
                points="40,8 72,68 8,68"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                className="text-foreground/25"
                strokeLinejoin="round"
              />
              <polygon
                points="40,18 62,60 18,60"
                fill="none"
                stroke="currentColor"
                strokeWidth="0.5"
                className="text-foreground/10"
                strokeLinejoin="round"
              />
            </svg>

            {/* Scan line */}
            <div className="absolute left-0 right-0 h-[1px] bg-foreground/15"
              style={{ animation: 'scan-line 3s ease-in-out infinite' }}
            />
          </div>
        </div>

        {/* Orbiting dots */}
        {[0, 1, 2, 3, 4, 5].map(i => (
          <div key={i} className="absolute" style={{
            width: '100%', height: '100%',
            animation: `orbit ${6 + i * 0.5}s linear infinite`,
            animationDelay: `${i * -1}s`,
          }}>
            <div className="absolute top-0 left-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-foreground"
              style={{
                width: `${3 - i * 0.3}px`,
                height: `${3 - i * 0.3}px`,
                opacity: 0.15 - i * 0.02,
              }}
            />
          </div>
        ))}
      </div>

      {/* Status text with smooth transition */}
      <div className="text-center max-w-sm relative z-10">
        <p className="text-[15px] text-foreground/50 font-body leading-relaxed mb-3 min-h-[24px]"
          style={{ animation: 'status-fade 0.5s ease-in-out' }}
          key={status}>
          {status}
        </p>

        {STUDIO_MODE && (
          <div className="mt-5 text-left">
            <div className="h-[3px] w-full rounded-full bg-foreground/[0.07] overflow-hidden">
              {/* Real progress only: finished stages out of all of them, never full until the job is done */}
              <div className="h-full rounded-full bg-foreground/40 transition-all duration-700"
                style={{ width: `${Math.min(doneCount(progress) / visibleStages(progress).length, 0.95) * 100}%` }} />
            </div>
            <p className="text-[11px] text-foreground/30 font-body mt-2 text-center">
              {doneCount(progress)} of {visibleStages(progress).length} steps done
            </p>
            <ul className="mt-4 space-y-1.5">
              {visibleStages(progress).map(st => {
                const state = stageState(progress, st.id)
                return (
                  <li key={st.id} className={`flex items-center gap-2.5 text-[12px] font-body ${
                    state === 'pending' ? 'text-foreground/20' : state === 'running' ? 'text-foreground/70' : 'text-foreground/40'}`}>
                    <span className="w-3 flex justify-center">
                      {state === 'running'
                        ? <span className="w-2 h-2 rounded-full border border-foreground/50 border-t-transparent animate-spin" />
                        : state === 'pending' ? <span className="w-1.5 h-1.5 rounded-full bg-foreground/15" />
                        : <span className="text-green-600/70">{state === 'done' ? '✓' : '–'}</span>}
                    </span>
                    {st.label}
                  </li>
                )
              })}
            </ul>
            <p className="text-[12px] text-foreground/30 font-body mt-5 text-center leading-relaxed">
              Most analyses take 2–3 minutes. The first one can take longer while the signal models warm up.
            </p>
          </div>
        )}

        {/* Time estimate (simulated, so not shown in demo mode where the real steps are listed) */}
        {!STUDIO_MODE && <p className="text-[14px] text-foreground/35 font-body mt-3">
          {elapsed < 10
            ? 'This usually takes 2–3 minutes — sit tight'
            : elapsed < 60
            ? `Analysing your video — about ${Math.max(2 - Math.floor(elapsed / 60), 1)}–${Math.max(3 - Math.floor(elapsed / 60), 2)} min left`
            : elapsed < 120
            ? `Almost halfway — about ${Math.max(3 - Math.floor(elapsed / 60), 1)} min left`
            : elapsed < 180
            ? 'Wrapping up — just a little longer'
            : 'Taking a bit longer than usual — hang tight'}
        </p>}

        {/* Animated dots */}
        <div className="flex justify-center gap-1.5 mt-4">
          {[0, 1, 2].map(i => (
            <div key={i} className="w-1 h-1 rounded-full bg-foreground/20"
              style={{ animation: `typing-dot 1.5s ease-in-out ${i * 0.2}s infinite` }} />
          ))}
        </div>
      </div>

      {/* Rotating fact */}
      <div className="mt-14 max-w-sm text-center relative z-10">
        <p className="text-[10px] font-semibold tracking-widest uppercase text-foreground/15 mb-2">Did you know?</p>
        <p className="text-[12px] text-foreground/25 font-body leading-relaxed min-h-[36px]"
          style={{ animation: 'status-fade 0.5s ease-in-out' }}
          key={factIdx}>
          {FACTS[factIdx]}
        </p>
      </div>

      {/* What you'll get */}
      <div className="mt-12 flex items-center gap-6 text-[10px] text-foreground/15 font-body relative z-10">
        <span>10 parameters</span>
        <span className="w-px h-3 bg-border" />
        <span>Trending hashtags</span>
        <span className="w-px h-3 bg-border" />
        <span>Caption rewrite</span>
        <span className="w-px h-3 bg-border" />
        <span>Step-by-step fixes</span>
      </div>
    </div>
  )
}
