import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { getUser, getToken, fetchUser } from '@/lib/auth'
import { apiUrl, uploadUrl } from '@/lib/api'
import { getNextPlan } from '@/lib/plans'
import PrismProcessing from '@/components/PrismProcessing'

const PENDING_JOB_KEY = 'prysm_pending_job'

const NICHES = ['Comedy', 'Fitness', 'Education', 'Lifestyle', 'Food', 'Tech', 'Fashion', 'Travel', 'Finance', 'Business', 'Motivation', 'Gaming', 'Beauty', 'Music', 'Sports', 'Health & Wellness', 'Dance', 'DIY & Crafts', 'Photography', 'Entertainment', 'Marketing', 'Vlogs', 'Self Improvement', 'Memes', 'News & Politics']

const PROCESSING_STEPS_COMPETITOR = [
  { text: 'Downloading the reel from Instagram...', delay: 0 },
  { text: 'Scraping caption, hashtags & engagement...', delay: 8000 },
  { text: 'Extracting frames from hook zone...', delay: 15000 },
  { text: 'Detecting scene transitions...', delay: 22000 },
  { text: 'Listening to audio...', delay: 30000 },
  { text: 'Transcribing speech...', delay: 40000 },
  { text: 'Breaking down what makes this work...', delay: 55000 },
  { text: 'Building your takeaway report...', delay: 85000 },
  { text: 'Almost there — finalizing breakdown...', delay: 100000 },
]

type Intent = 'competitor'

export default function AnalyseLink() {
  const [step, setStep] = useState(1)
  const [direction, setDirection] = useState<'forward' | 'back'>('forward')
  const [intent] = useState<Intent>('competitor')
  const [selectedNiches, setSelectedNiches] = useState<string[]>([])
  const [customNiche, setCustomNiche] = useState('')
  const [showCustom, setShowCustom] = useState(false)
  const [url, setUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [brief, setBrief] = useState('')
  const [urlValid, setUrlValid] = useState(false)
  const [showUpgradeModal, setShowUpgradeModal] = useState(false)
  const [showExhaustedModal, setShowExhaustedModal] = useState(false)
  const [exhaustedPlan, setExhaustedPlan] = useState<string>('free')
  const inputRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()
  const [user, setUser] = useState(getUser())
  const nextPlan = getNextPlan(user?.plan)

  useEffect(() => {
    fetchUser().then(u => { if (u) setUser(u) })
  }, [])

  function isValidInstagramUrl(u: string): boolean {
    // Must be a proper Instagram reel/post URL with a valid shortcode (11 chars typically)
    const match = u.match(/instagram\.com\/(p|reel|reels)\/([A-Za-z0-9_-]+)/i)
    if (!match) return false
    const shortcode = match[2]
    // Real Instagram shortcodes are 11 characters (sometimes 10-12)
    // Reject obviously fake ones that are too short or too long
    if (shortcode.length < 8 || shortcode.length > 16) return false
    return true
  }

  // Auto-detect valid URL
  useEffect(() => {
    const valid = isValidInstagramUrl(url)
    setUrlValid(valid)
    if (valid) setError('')
  }, [url])

  function toggleNiche(n: string) {
    if (n === '__other__') {
      setShowCustom(!showCustom)
      return
    }
    setSelectedNiches(prev =>
      prev.includes(n) ? prev.filter(x => x !== n) : prev.length < 3 ? [...prev, n] : prev
    )
  }

  function addCustomNiche() {
    const trimmed = customNiche.trim()
    if (!trimmed || selectedNiches.length >= 3) return
    if (!selectedNiches.includes(trimmed)) {
      setSelectedNiches(prev => [...prev, trimmed])
    }
    setCustomNiche('')
    setShowCustom(false)
  }

  function goTo(s: number) {
    setDirection(s > step ? 'forward' : 'back')
    setStep(s)
    setError('')
  }

  function handleNicheNext() {
    if (selectedNiches.length === 0) { setError('Pick at least one niche'); return }
    goTo(4)
  }

  async function handleSubmit() {
    if (!brief.trim()) { setError('Tell us a bit about the video'); return }

    // Pre-check credits before starting the long download + analysis
    if (user && user.credits < 3) {
      setShowUpgradeModal(true)
      return
    }

    setError('')
    setLoading(true)

    const steps = PROCESSING_STEPS_COMPETITOR
    setStatus(steps[0].text)
    const timers = steps.slice(1).map(s => setTimeout(() => setStatus(st => st ? s.text : st), s.delay))

    try {
      const formData = new FormData()
      formData.append('url', url.trim())
      formData.append('niche', selectedNiches.join(', '))
      formData.append('platform', 'Instagram Reels')
      formData.append('brief', brief.trim())
      formData.append('intent', intent)
      const headers: Record<string, string> = {}
      const tk = getToken()
      if (tk) headers['Authorization'] = `Bearer ${tk}`

      // Step 1: Submit — returns job_id in ~5 seconds
      const submitRes = await fetch(uploadUrl('/prysm/analyse'), { method: 'POST', body: formData, headers })

      if (submitRes.status === 402) {
        setError('')
        setLoading(false)
        setStatus('')
        setShowUpgradeModal(true)
        timers.forEach(clearTimeout)
        return
      }
      if (!submitRes.ok) {
        const errData = await submitRes.json().catch(() => ({}))
        throw new Error(errData.detail || errData.error || `Server error: ${submitRes.status}`)
      }

      const submitData = await submitRes.json()
      if (submitData.error) throw new Error(submitData.error)
      const jobId = submitData.job_id
      if (!jobId) throw new Error('No job ID from server — please try again')

      // Persist job so a page refresh can resume polling
      localStorage.setItem(PENDING_JOB_KEY, JSON.stringify({ jobId, source: 'link', intent, startedAt: Date.now() }))

      await pollJob(jobId, intent, headers, timers)
    } catch (err: any) {
      localStorage.removeItem(PENDING_JOB_KEY)
      setError(err.message || 'Analysis failed — please try again')
      setLoading(false)
      setStatus('')
    } finally {
      timers.forEach(clearTimeout)
    }
  }

  async function pollJob(jobId: string, jobIntent: string, headers: Record<string, string>, timers: ReturnType<typeof setTimeout>[]) {
    try {
      const pollStart = Date.now()
      let consecutiveErrors = 0
      while (Date.now() - pollStart < 15 * 60 * 1000) {
        await new Promise(r => setTimeout(r, 5000))
        try {
          const statusRes = await fetch(apiUrl(`/prysm/analyse/status/${jobId}`), { headers })
          if (!statusRes.ok) { consecutiveErrors++; continue }
          consecutiveErrors = 0
          const statusData = await statusRes.json()

          if (statusData.status === 'complete' && statusData.result) {
            const data = statusData.result
            localStorage.setItem('prysm_video_source', 'link')
            localStorage.setItem('prysm_intent', jobIntent)
            localStorage.setItem('prysm_result', JSON.stringify(data))
            localStorage.removeItem(PENDING_JOB_KEY)
            setStatus('Done!')
            timers.forEach(clearTimeout)
            setTimeout(() => navigate('/results'), 500)
            return
          }
          if (statusData.status === 'credits_exhausted') {
            localStorage.removeItem(PENDING_JOB_KEY)
            timers.forEach(clearTimeout)
            setLoading(false)
            setStatus('')
            setExhaustedPlan(statusData.plan || 'free')
            setShowExhaustedModal(true)
            return
          }
          if (statusData.status === 'failed') {
            throw new Error(statusData.error || 'Analysis failed — please try again')
          }
        } catch (pollErr: any) {
          if (pollErr.message && !pollErr.message.includes('fetch') && !pollErr.message.includes('network') && !pollErr.message.includes('NetworkError')) {
            throw pollErr
          }
          consecutiveErrors++
          if (consecutiveErrors >= 10) throw new Error('Lost connection to server. Check your dashboard — your analysis may have completed.')
        }
      }
      // Timed out — analysis is probably still running or completed. Send to dashboard.
      localStorage.removeItem(PENDING_JOB_KEY)
      timers.forEach(clearTimeout)
      setLoading(false)
      setStatus('')
      navigate('/dashboard')
    } finally {
      timers.forEach(clearTimeout)
    }
  }

  // On mount: resume polling if a job was in-flight when the page was last closed
  useEffect(() => {
    const raw = localStorage.getItem(PENDING_JOB_KEY)
    if (!raw) return
    try {
      const { jobId, source, intent: savedIntent, startedAt } = JSON.parse(raw)
      if (source !== 'link') return // upload page handles upload jobs
      const elapsed = Date.now() - startedAt
      if (!jobId || elapsed > 15 * 60 * 1000) { localStorage.removeItem(PENDING_JOB_KEY); return }
      const headers: Record<string, string> = {}
      const tk = getToken()
      if (tk) headers['Authorization'] = `Bearer ${tk}`
      setLoading(true)
      setStatus('Resuming analysis...')
      const resumeTimers: ReturnType<typeof setTimeout>[] = []
      pollJob(jobId, savedIntent || 'competitor', headers, resumeTimers).catch(err => {
        localStorage.removeItem(PENDING_JOB_KEY)
        setError(err.message || 'Analysis failed — please try again')
        setLoading(false)
        setStatus('')
      })
    } catch {
      localStorage.removeItem(PENDING_JOB_KEY)
    }
  }, [])

  if (loading) return <PrismProcessing status={status} />

  // Slide animation class
  const slideClass = direction === 'forward'
    ? 'animate-[slideInRight_0.4s_ease-out]'
    : 'animate-[slideInLeft_0.4s_ease-out]'

  return (
    <div className="min-h-screen bg-background text-foreground overflow-hidden">
      {/* Nav — minimal */}
      <nav className="px-3 sm:px-8 h-14 flex items-center justify-between">
        <a href="/" className="font-logo text-[18px] sm:text-[21px] text-foreground">PRYSM</a>
        <div className="flex items-center gap-2 sm:gap-4">
          <a href="/analyse" className="text-xs sm:text-sm text-foreground/30 hover:text-foreground/50 transition-colors">← Back</a>
        </div>
      </nav>

      {/* Low-credit warning banner */}
      {user && user.credits > 0 && user.credits < 10 && (
        <div className="mx-3 sm:mx-8 mb-2 px-4 py-2.5 rounded-xl bg-amber-50/60 border border-amber-200/50 flex items-center justify-between gap-3">
          <p className="text-[12px] font-body text-amber-700/70">
            <span className="font-semibold">Running low —</span> {Math.round(user.credits * 10) / 10} credits left. Each analysis uses your credits.
          </p>
          <a href="/pricing" className="text-[11px] font-bold font-body text-amber-700/60 hover:text-amber-800 transition-colors shrink-0">Upgrade →</a>
        </div>
      )}

      {/* Progress dots */}
      <div className="flex justify-center gap-2 mt-4 mb-8">
        {[1, 3, 4].map(s => (
          <button
            key={s}
            onClick={() => { if (s < step) goTo(s) }}
            className={`h-1.5 rounded-full transition-all duration-500 ${
              s === step ? 'w-8 bg-foreground' :
              s < step ? 'w-3 bg-foreground/30 cursor-pointer hover:bg-foreground/50' :
              'w-3 bg-foreground/10'
            }`}
          />
        ))}
      </div>

      {/* Step container */}
      <div className="max-w-lg mx-auto px-6">

        {/* ═══════════════ STEP 1: URL ═══════════════ */}
        {step === 1 && (
          <div key="step1" className={slideClass}>
            <div className="text-center mb-10">
              <p className="text-[11px] font-semibold tracking-[0.2em] uppercase text-foreground/25 mb-4 font-body">Step 1</p>
              <h1 className="font-display text-4xl font-bold tracking-tight mb-3">
                Drop the link
              </h1>
              <p className="text-foreground/40 font-body text-base">
                Paste any Instagram Reel or Post URL
              </p>
            </div>

            <div className="relative group">
              <div className={`absolute -inset-1 rounded-2xl transition-all duration-500 ${
                urlValid ? 'bg-green-500/10 blur-lg' : 'bg-foreground/5 blur-lg opacity-0 group-hover:opacity-100'
              }`} />
              <div className="relative">
                <input
                  ref={inputRef}
                  type="text"
                  placeholder="https://www.instagram.com/reel/..."
                  value={url}
                  onChange={e => { setUrl(e.target.value); setError('') }}
                  autoFocus
                  className={`w-full border-2 rounded-2xl px-6 py-5 text-base text-foreground placeholder:text-foreground/20 outline-none transition-all font-body bg-background ${
                    urlValid ? 'border-green-500/40 shadow-[0_0_30px_rgba(34,197,94,0.08)]' : 'border-border focus:border-foreground/30'
                  }`}
                />
                <button
                  onClick={async () => {
                    try { const text = await navigator.clipboard.readText(); setUrl(text) } catch {}
                  }}
                  className="absolute right-4 top-1/2 -translate-y-1/2 px-3 py-1.5 rounded-lg bg-foreground/5 text-[12px] text-foreground/40 font-body cursor-pointer hover:bg-foreground/10 transition-colors flex items-center gap-1.5"
                >
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M15.666 3.888A2.25 2.25 0 0013.5 2.25h-3c-1.03 0-1.9.693-2.166 1.638m7.332 0c.055.194.084.4.084.612v0a.75.75 0 01-.75.75H9.75a.75.75 0 01-.75-.75v0c0-.212.03-.418.084-.612m7.332 0c.646.049 1.288.11 1.927.184 1.1.128 1.907 1.077 1.907 2.185V19.5a2.25 2.25 0 01-2.25 2.25H6.75A2.25 2.25 0 014.5 19.5V6.257c0-1.108.806-2.057 1.907-2.185a48.208 48.208 0 011.927-.184" />
                  </svg>
                  Paste
                </button>
              </div>
            </div>

            {/* URL feedback */}
            <div className="h-8 mt-4 flex justify-center">
              {urlValid && (
                <div className="flex items-center gap-2 animate-[fadeIn_0.3s_ease-out]">
                  <span className="w-2 h-2 rounded-full bg-green-500" />
                  <span className="text-sm text-green-600/70 font-body">Looks good — we'll verify when we fetch it</span>
                </div>
              )}
              {url && !urlValid && url.length > 10 && (
                <p className="text-sm text-red-500/60 font-body">
                  {url.includes('instagram.com') ? 'Paste a link to a specific reel or post (e.g. instagram.com/reel/ABC123...)' : 'Not an Instagram URL'}
                </p>
              )}
            </div>

            {error && <p className="text-sm text-red-500 font-body text-center mt-2">{error}</p>}

            {/* Continue */}
            <button
              onClick={() => {
                if (!url.trim()) { setError('Paste a URL first'); return }
                if (!urlValid) { setError('Please paste a valid Instagram URL'); return }
                goTo(3)
              }}
              disabled={!urlValid}
              className={`w-full mt-6 py-4 rounded-2xl font-display font-semibold text-[15px] transition-all ${
                urlValid
                  ? 'bg-foreground text-background cursor-pointer hover:opacity-90 shadow-lg shadow-foreground/10'
                  : 'bg-foreground/5 text-foreground/20 cursor-not-allowed'
              }`}
            >
              Continue →
            </button>
          </div>
        )}



        {/* ═══════════════ STEP 3: NICHE ═══════════════ */}
        {step === 3 && (
          <div key="step3" className={slideClass}>
            <div className="text-center mb-10">
              <p className="text-[11px] font-semibold tracking-[0.2em] uppercase text-foreground/25 mb-4 font-body">Step 3</p>
              <h1 className="font-display text-4xl font-bold tracking-tight mb-3">
                {intent === 'competitor' ? "What's your niche?" : "What's your niche?"}
              </h1>
              <p className="text-foreground/40 font-body text-base">
                {intent === 'competitor' ? 'So we tailor takeaways to your content style' : 'Pick up to 3 that describe your content'}
              </p>
            </div>

            <div className="flex flex-wrap gap-2.5 justify-center">
              {NICHES.map(n => (
                <button
                  key={n}
                  onClick={() => toggleNiche(n)}
                  className={`px-5 py-2.5 rounded-full text-sm font-medium border-2 transition-all duration-200 cursor-pointer ${
                    selectedNiches.includes(n)
                      ? 'bg-foreground text-background border-foreground scale-105 shadow-md shadow-foreground/10'
                      : 'bg-transparent text-foreground/40 border-border hover:border-foreground/25 hover:text-foreground/60 hover:scale-[1.02]'
                  }`}
                  style={selectedNiches.includes(n) ? { animation: 'popIn 0.25s ease-out' } : undefined}
                >
                  {n}
                </button>
              ))}
              <button
                onClick={() => toggleNiche('__other__')}
                className={`px-5 py-2.5 rounded-full text-sm font-medium border-2 transition-all duration-200 cursor-pointer ${
                  showCustom || selectedNiches.some(n => !NICHES.includes(n))
                    ? 'bg-foreground text-background border-foreground scale-105 shadow-md shadow-foreground/10'
                    : 'bg-transparent text-foreground/40 border-border hover:border-foreground/25 hover:text-foreground/60 hover:scale-[1.02]'
                }`}
              >
                Other
              </button>
            </div>

            {showCustom && (
              <div className="flex gap-2 mt-4 max-w-xs mx-auto">
                <input
                  autoFocus
                  type="text"
                  value={customNiche}
                  onChange={e => setCustomNiche(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') addCustomNiche() }}
                  placeholder="Type your niche..."
                  className="flex-1 px-4 py-2.5 rounded-xl border border-border bg-background text-sm text-foreground placeholder:text-foreground/25 outline-none focus:border-foreground/30 font-body transition-colors"
                />
                <button
                  onClick={addCustomNiche}
                  disabled={!customNiche.trim() || selectedNiches.length >= 3}
                  className="px-4 py-2.5 rounded-xl bg-foreground text-background text-sm font-body font-medium disabled:opacity-30 cursor-pointer hover:opacity-90 transition-opacity"
                >
                  Add
                </button>
              </div>
            )}

            {selectedNiches.length > 0 && (
              <p className="text-center text-xs text-foreground/25 font-body mt-4">
                {selectedNiches.join(', ')} {selectedNiches.length < 3 && '· pick up to 3'}
              </p>
            )}

            {error && <p className="text-sm text-red-500 font-body text-center mt-3">{error}</p>}

            <div className="flex gap-3 mt-8">
              <button onClick={() => goTo(1)} className="flex-1 py-3.5 rounded-2xl border border-border text-foreground/40 text-sm font-body cursor-pointer hover:bg-secondary/50 transition-colors">
                ← Back
              </button>
              <button
                onClick={handleNicheNext}
                disabled={selectedNiches.length === 0}
                className={`flex-[2] py-3.5 rounded-2xl font-display font-semibold text-[15px] transition-all ${
                  selectedNiches.length > 0
                    ? 'bg-foreground text-background cursor-pointer hover:opacity-90 shadow-lg shadow-foreground/10'
                    : 'bg-foreground/5 text-foreground/20 cursor-not-allowed'
                }`}
              >
                Continue →
              </button>
            </div>
          </div>
        )}

        {/* ═══════════════ STEP 4: BRIEF + GOAL + SUBMIT ═══════════════ */}
        {step === 4 && (
          <div key="step4" className={slideClass}>
            <div className="text-center mb-8">
              <p className="text-[11px] font-semibold tracking-[0.2em] uppercase text-foreground/25 mb-4 font-body">Final step</p>
              <h1 className="font-display text-4xl font-bold tracking-tight mb-3">
                {intent === 'competitor' ? 'What caught your eye?' : 'Tell us about it'}
              </h1>
              <p className="text-foreground/40 font-body text-base">
                What do you want to learn from this video?
              </p>
            </div>

            <textarea
              value={brief}
              onChange={e => { setBrief(e.target.value); setError('') }}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && brief.trim()) { e.preventDefault(); handleSubmit() } }}
              placeholder={"What do you want to learn from this video?\ne.g. \"how they get so many views\" or \"I want to make content like this\""}
              rows={3}
              className="w-full border-2 border-border rounded-2xl px-5 py-4 text-sm text-foreground placeholder:text-foreground/18 outline-none focus:border-foreground/25 transition-all resize-none font-body bg-background"
            />

            {error && (
              <div className="mt-4 px-4 py-3 rounded-xl bg-red-50/50 border border-red-200/50">
                <p className="text-red-600 text-sm font-body">{error}</p>
                {error.toLowerCase().includes('download') && (
                  <a href="/analyse/upload" className="text-sm text-red-500/60 underline hover:text-red-600 transition-colors mt-1 inline-block font-body">
                    Switch to Upload Raw Video →
                  </a>
                )}
              </div>
            )}

            <div className="flex gap-3 mt-8">
              <button onClick={() => goTo(3)} className="flex-1 py-3.5 rounded-2xl border border-border text-foreground/40 text-sm font-body cursor-pointer hover:bg-secondary/50 transition-colors">
                ← Back
              </button>
              <button
                onClick={handleSubmit}
                disabled={!brief.trim()}
                className={`flex-[2] py-4 rounded-2xl font-display font-bold text-base transition-all relative overflow-hidden ${
                  brief.trim()
                    ? 'bg-foreground text-background cursor-pointer hover:shadow-xl hover:shadow-foreground/15 hover:scale-[1.01] active:scale-[0.99]'
                    : 'bg-foreground/5 text-foreground/20 cursor-not-allowed'
                }`}
              >
                Break Down This Video →
              </button>
            </div>

            {/* Subtle reassurance */}
            <div className="mt-8 flex items-center justify-center gap-6 text-[10px] text-foreground/20 font-body">
              <span>10 parameters</span>
              <span className="w-px h-3 bg-border" />
              <span>Format breakdown</span>
              <span className="w-px h-3 bg-border" />
              <span>Replicable takeaways</span>
            </div>
          </div>
        )}
      </div>

      {/* ═══ CREDITS EXHAUSTED MID-ANALYSIS MODAL ═══ */}
      {showExhaustedModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-[100] flex items-center justify-center p-6">
          <div className="bg-background border border-border rounded-2xl p-7 max-w-sm w-full shadow-2xl text-center"
            style={{ animation: 'fade-slide-in 0.3s ease-out' }}>
            <div className="w-14 h-14 rounded-full bg-amber-50 border border-amber-200/40 flex items-center justify-center mx-auto mb-5">
              <svg className="w-6 h-6 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
              </svg>
            </div>
            <h2 className="font-display text-xl font-bold mb-2">Your credits ran out</h2>
            <p className="text-[14px] text-foreground/45 font-body mb-6 leading-relaxed">
              {exhaustedPlan === 'free'
                ? `Prysm broke down this reel but your free credits ran out before the results could be unlocked. Upgrade to Pro to get more credits and see your results.`
                : `Prysm broke down this reel but you ran out of credits mid-way. Your credits will renew next cycle — or upgrade to a higher plan to get more now.`}
            </p>
            <div className="flex flex-col gap-2">
              <a href="/pricing" className="block w-full py-3 rounded-xl bg-foreground text-background text-[14px] font-body font-bold hover:opacity-90 transition-opacity">
                {exhaustedPlan === 'free' ? 'Upgrade to Pro' : 'View plans'}
              </a>
              <button onClick={() => setShowExhaustedModal(false)}
                className="w-full py-2.5 rounded-xl text-[13px] text-foreground/40 font-body hover:text-foreground/60 transition-colors cursor-pointer">
                Maybe later
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ═══ UPGRADE MODAL ═══ */}
      {showUpgradeModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-[100] flex items-center justify-center p-6"
          onClick={() => setShowUpgradeModal(false)}>
          <div onClick={e => e.stopPropagation()}
            className="bg-background border border-border rounded-2xl p-7 max-w-sm w-full shadow-2xl text-center"
            style={{ animation: 'fade-slide-in 0.3s ease-out' }}>
            <div className="w-14 h-14 rounded-full bg-amber-50 border border-amber-200/40 flex items-center justify-center mx-auto mb-5">
              <svg className="w-6 h-6 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
              </svg>
            </div>
            <h2 className="font-display text-xl font-bold mb-2">You're out of credits</h2>
            <p className="text-[14px] text-foreground/45 font-body mb-6 leading-relaxed">
              {nextPlan
                ? `Upgrade to ${nextPlan.name} for ${nextPlan.price} to get more credits and unlock premium features.`
                : 'You have used all your available credits.'}
            </p>
            <div className="flex flex-col gap-2">
              <a href="/pricing" className="block w-full py-3 rounded-xl bg-foreground text-background text-[14px] font-body font-bold hover:opacity-90 transition-opacity">
                {nextPlan ? `Upgrade to ${nextPlan.name}` : 'View plans'}
              </a>
              <button onClick={() => setShowUpgradeModal(false)}
                className="w-full py-2.5 rounded-xl text-[13px] text-foreground/40 font-body hover:text-foreground/60 transition-colors cursor-pointer">
                Maybe later
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
