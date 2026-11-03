import { useRef, useState } from 'react'
import html2canvas from 'html2canvas'

interface ShareCardProps {
  overallScore: number
  scoreLabel: string
  parameters: { name: string; score: number }[]
  username: string
}

const ShareCard = ({ overallScore, scoreLabel, parameters, username }: ShareCardProps) => {
  const cardRef = useRef<HTMLDivElement>(null)
  const [generating, setGenerating] = useState(false)
  const [preview, setPreview] = useState(false)

  // Only show parameters scoring 70+, fallback to top 3 if none qualify
  const strongParams = parameters.filter(p => p.score >= 70).sort((a, b) => b.score - a.score).slice(0, 4)
  const displayParams = strongParams.length >= 2
    ? strongParams
    : [...parameters].sort((a, b) => b.score - a.score).slice(0, 3)
  const strengthLabel = strongParams.length >= 2 ? 'Your Strengths' : 'Your Strongest Areas'

  const ringOffset = 251 - (251 * overallScore / 100)
  const ringColor = overallScore >= 70 ? '#22c55e' : overallScore >= 50 ? '#f5c542' : '#ef4444'

  const badgeStyle = overallScore >= 80
    ? { bg: 'rgba(34,197,94,0.15)', border: 'rgba(34,197,94,0.3)', color: '#4ade80' }
    : overallScore >= 60
    ? { bg: 'rgba(245,197,66,0.15)', border: 'rgba(245,197,66,0.3)', color: '#f5c542' }
    : { bg: 'rgba(239,68,68,0.15)', border: 'rgba(239,68,68,0.3)', color: '#f87171' }

  const generateImage = async () => {
    if (!cardRef.current) return
    setGenerating(true)
    try {
      const canvas = await html2canvas(cardRef.current, {
        scale: 2,
        useCORS: true,
        backgroundColor: null,
        logging: false,
      })
      const blob = await new Promise<Blob>((resolve) =>
        canvas.toBlob((b) => resolve(b!), 'image/png', 1.0)
      )
      const file = new File([blob], 'prysm-score.png', { type: 'image/png' })

      // Try native share (mobile)
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file] })
      } else {
        // Desktop fallback — download
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = 'prysm-score.png'
        a.click()
        URL.revokeObjectURL(url)
      }
    } catch (e) {
      console.error('Share failed:', e)
    } finally {
      setGenerating(false)
    }
  }

  return (
    <>
      <button
        onClick={() => setPreview(true)}
        className="inline-flex items-center gap-2 px-5 py-2.5 rounded-full border border-border bg-background hover:bg-secondary/50 transition-all duration-200 text-[13px] font-body font-semibold text-foreground/70 hover:text-foreground"
      >
        <span style={{ fontSize: 14 }}>✦</span>
        Share your score
      </button>

      {/* Preview overlay */}
      {preview && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
          onClick={(e) => { if (e.target === e.currentTarget) setPreview(false) }}>
          <div className="flex flex-col items-center gap-4 max-w-sm w-full">

            {/* The actual card that gets captured */}
            <div ref={cardRef} style={{
              width: 360,
              height: 640,
              background: 'linear-gradient(160deg, #0a0a0a 0%, #1a1a18 40%, #0f0f0d 100%)',
              borderRadius: 24,
              padding: '40px 32px',
              display: 'flex',
              flexDirection: 'column',
              position: 'relative',
              overflow: 'hidden',
              fontFamily: "'General Sans', 'Manrope', sans-serif",
            }}>
              {/* Subtle grain texture overlay */}
              <div style={{
                position: 'absolute', inset: 0,
                background: 'url("data:image/svg+xml,%3Csvg viewBox=\'0 0 256 256\' xmlns=\'http://www.w3.org/2000/svg\'%3E%3Cfilter id=\'noise\'%3E%3CfeTurbulence type=\'fractalNoise\' baseFrequency=\'0.9\' numOctaves=\'4\' stitchTiles=\'stitch\'/%3E%3C/filter%3E%3Crect width=\'100%25\' height=\'100%25\' filter=\'url(%23noise)\' opacity=\'0.03\'/%3E%3C/svg%3E")',
                pointerEvents: 'none',
              }} />

              {/* Warm glow behind score */}
              <div style={{
                position: 'absolute',
                top: '25%', left: '50%',
                transform: 'translate(-50%, -50%)',
                width: 200, height: 200,
                background: `radial-gradient(circle, ${ringColor}15 0%, transparent 70%)`,
                pointerEvents: 'none',
              }} />

              {/* Logo */}
              <div style={{
                fontFamily: "'Outfit', sans-serif",
                fontSize: 18,
                fontWeight: 900,
                color: 'rgba(255,255,255,0.5)',
                letterSpacing: '0.02em',
                textTransform: 'uppercase',
                marginBottom: 32,
              }}>
                PRYSM
              </div>

              {/* Score ring */}
              <div style={{
                display: 'flex',
                justifyContent: 'center',
                marginBottom: 16,
              }}>
                <div style={{ position: 'relative', width: 120, height: 120 }}>
                  <svg viewBox="0 0 100 100" style={{ width: '100%', height: '100%', transform: 'rotate(-90deg)' }}>
                    <circle cx="50" cy="50" r="40" fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="7" />
                    <circle cx="50" cy="50" r="40" fill="none" stroke={ringColor} strokeWidth="7"
                      strokeLinecap="round" strokeDasharray="251" strokeDashoffset={ringOffset} />
                  </svg>
                  <div style={{
                    position: 'absolute', inset: 0,
                    display: 'flex', flexDirection: 'column',
                    alignItems: 'center', justifyContent: 'center',
                  }}>
                    <span style={{ fontSize: 36, fontWeight: 800, color: '#fff', letterSpacing: '-0.03em' }}>
                      {overallScore}
                    </span>
                    <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.3)', fontFamily: "'Manrope', sans-serif" }}>
                      / 100
                    </span>
                  </div>
                </div>
              </div>

              {/* Badge */}
              <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 32 }}>
                <span style={{
                  fontSize: 9,
                  fontWeight: 700,
                  letterSpacing: '0.1em',
                  textTransform: 'uppercase',
                  padding: '5px 14px',
                  borderRadius: 999,
                  background: badgeStyle.bg,
                  border: `1px solid ${badgeStyle.border}`,
                  color: badgeStyle.color,
                  fontFamily: "'Manrope', sans-serif",
                }}>
                  {scoreLabel}
                </span>
              </div>

              {/* Strengths */}
              <div style={{ flex: 1 }}>
                <div style={{
                  fontSize: 9,
                  fontWeight: 700,
                  letterSpacing: '0.12em',
                  textTransform: 'uppercase',
                  color: 'rgba(255,255,255,0.25)',
                  marginBottom: 14,
                  fontFamily: "'Manrope', sans-serif",
                }}>
                  {strengthLabel}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  {displayParams.map((p) => (
                    <div key={p.name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <span style={{
                        fontSize: 13,
                        color: 'rgba(255,255,255,0.55)',
                        fontFamily: "'Manrope', sans-serif",
                        fontWeight: 500,
                      }}>
                        {p.name}
                      </span>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{
                          fontSize: 14,
                          fontWeight: 700,
                          color: p.score >= 70 ? '#4ade80' : p.score >= 55 ? '#f5c542' : '#f87171',
                          fontFamily: "'General Sans', sans-serif",
                        }}>
                          {p.score}
                        </span>
                        <div style={{
                          width: 6, height: 6, borderRadius: '50%',
                          background: p.score >= 70 ? '#22c55e' : p.score >= 55 ? '#eab308' : '#ef4444',
                        }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Footer */}
              <div style={{
                borderTop: '1px solid rgba(255,255,255,0.06)',
                paddingTop: 20,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
              }}>
                <span style={{
                  fontSize: 13,
                  color: 'rgba(255,255,255,0.4)',
                  fontFamily: "'Manrope', sans-serif",
                  fontWeight: 600,
                }}>
                  @{username}
                </span>
                <span style={{
                  fontSize: 10,
                  color: 'rgba(255,255,255,0.2)',
                  fontFamily: "'Manrope', sans-serif",
                  fontWeight: 500,
                }}>
                  Analyzed by Prysm
                </span>
              </div>
            </div>

            {/* Action buttons */}
            <div className="flex gap-3 w-full" style={{ maxWidth: 360 }}>
              <button
                onClick={generateImage}
                disabled={generating}
                className="flex-1 py-3 rounded-xl text-sm font-bold font-body bg-white text-black hover:bg-white/90 transition-colors disabled:opacity-50"
              >
                {generating ? 'Generating...' : 'Share to Stories'}
              </button>
              <button
                onClick={() => setPreview(false)}
                className="px-5 py-3 rounded-xl text-sm font-body font-medium text-white/50 border border-white/10 hover:border-white/25 transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

export default ShareCard
