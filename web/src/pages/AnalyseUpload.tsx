import { useState, useRef, useCallback, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { getUser, getToken, fetchUser } from '@/lib/auth'
import { apiUrl, uploadUrl } from '@/lib/api'
import { saveVideo } from '@/lib/videoStore'
import { getNextPlan } from '@/lib/plans'
import PrismProcessing from '@/components/PrismProcessing'

const PENDING_JOB_KEY = 'prysm_pending_job'

const NICHES = ['Comedy', 'Fitness', 'Education', 'Lifestyle', 'Food', 'Tech', 'Fashion', 'Travel', 'Finance', 'Business', 'Motivation', 'Gaming', 'Beauty', 'Music', 'Sports', 'Health & Wellness', 'Dance', 'DIY & Crafts', 'Photography', 'Entertainment', 'Marketing', 'Vlogs', 'Self Improvement', 'Memes', 'News & Politics']

const PROCESSING_STEPS = [
  { text: 'Uploading your video...', delay: 0 },
  { text: 'Extracting frames from hook zone...', delay: 8000 },
  { text: 'Detecting scene transitions...', delay: 15000 },
  { text: 'Listening to your audio...', delay: 22000 },
  { text: 'Transcribing speech...', delay: 30000 },
  { text: 'Analyzing 10 parameters...', delay: 40000 },
  { text: 'Checking trending hashtags in your niche...', delay: 55000 },
  { text: 'Writing your personalized suggestions...', delay: 70000 },
  { text: 'Almost there — finalizing your report...', delay: 90000 },
]

export default function AnalyseUpload() {
  const [step, setStep] = useState(1)
  const [direction, setDirection] = useState<'forward' | 'back'>('forward')
  const [file, setFile] = useState<File | null>(null)
  const [videoPreview, setVideoPreview] = useState<string | null>(null)
  const [selectedNiches, setSelectedNiches] = useState<string[]>([])
  const [customNiche, setCustomNiche] = useState('')
  const [showCustom, setShowCustom] = useState(false)
  const [brief, setBrief] = useState('')
  const [platform, setPlatform] = useState('Instagram Reels')
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [showUpgradeModal, setShowUpgradeModal] = useState(false)
  const [showExhaustedModal, setShowExhaustedModal] = useState(false)
  const [exhaustedPlan, setExhaustedPlan] = useState<string>('free')
  const fileRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()
  const [user, setUser] = useState(getUser())
  const nextPlan = getNextPlan(user?.plan)

  useEffect(() => {
    fetchUser().then(u => { if (u) setUser(u) })
  }, [])

  function goTo(s: number) {
    setDirection(s > step ? 'forward' : 'back')
    setStep(s)
    setError('')
  }

  function handleFile(f: File) {
    if (!f.type.startsWith('video/')) { setError('Please select a video file'); return }
    if (f.size > 500 * 1024 * 1024) { setError('File too large — max 500MB'); return }
    setFile(f)
    setError('')
    // Create preview thumbnail
    const url = URL.createObjectURL(f)
    setVideoPreview(url)
    // Auto-advance after a brief moment
    setTimeout(() => goTo(2), 600)
  }

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }, [])

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

  async function handleSubmit() {
    if (!file) { setError('Please upload a video'); return }
    if (selectedNiches.length === 0) { setError('Please select a niche'); return }
    if (!brief.trim()) { setError('Tell us about your video'); return }

    // Pre-check credits before starting the long upload + analysis
    if (user && user.credits < 3) {
      setShowUpgradeModal(true)
      return
    }

    setError('')
    setLoading(true)
    setStatus(PROCESSING_STEPS[0].text)
    const timers = PROCESSING_STEPS.slice(1).map(s => setTimeout(() => setStatus(st => st ? s.text : st), s.delay))

    try {
      const formData = new FormData()
      formData.append('video', file)
      formData.append('niche', selectedNiches.join(', '))
      formData.append('platform', platform)
      formData.append('brief', brief.trim())
      formData.append('intent', 'my_video')
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
      localStorage.setItem(PENDING_JOB_KEY, JSON.stringify({ jobId, source: 'upload', intent: 'my_video', startedAt: Date.now() }))

      await pollJob(jobId, headers, timers, file)
    } catch (err: any) {
      localStorage.removeItem(PENDING_JOB_KEY)
      setError(err.message || 'Analysis failed — please try again')
      setLoading(false)
      setStatus('')
    } finally {
      timers.forEach(clearTimeout)
    }
  }

  async function pollJob(jobId: string, headers: Record<string, string>, timers: ReturnType<typeof setTimeout>[], uploadedFile?: File | null) {
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
            if (uploadedFile) await saveVideo(uploadedFile, data.analysis_id || jobId)
            localStorage.setItem('prysm_video_source', 'upload')
            localStorage.setItem('prysm_intent', 'my_video')
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
      const { jobId, startedAt } = JSON.parse(raw)
      const elapsed = Date.now() - startedAt
      if (!jobId || elapsed > 15 * 60 * 1000) { localStorage.removeItem(PENDING_JOB_KEY); return }
      const headers: Record<string, string> = {}
      const tk = getToken()
      if (tk) headers['Authorization'] = `Bearer ${tk}`
      setLoading(true)
      setStatus('Resuming analysis...')
      const resumeTimers: ReturnType<typeof setTimeout>[] = []
      pollJob(jobId, headers, resumeTimers, null).catch(err => {
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

  const slideClass = direction === 'forward'
    ? 'animate-[slideInRight_0.4s_ease-out]'
    : 'animate-[slideInLeft_0.4s_ease-out]'

  return (
    <div className="min-h-screen bg-background text-foreground overflow-hidden">
      {/* Nav */}
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
        {[1, 2, 3].map(s => (
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

      <div className="max-w-lg mx-auto px-6">

        {/* ═══════════════ STEP 1: UPLOAD ═══════════════ */}
        {step === 1 && (
          <div key="step1" className={slideClass}>
            <div className="text-center mb-10">
              <p className="text-[11px] font-semibold tracking-[0.2em] uppercase text-foreground/25 mb-4 font-body">Step 1</p>
              <h1 className="font-display text-4xl font-bold tracking-tight mb-3">
                Upload your video
              </h1>
              <p className="text-foreground/40 font-body text-base">
                Drop your raw video — we'll handle the rest
              </p>
            </div>

            {/* Drag & drop zone */}
            <div
              onDragOver={e => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={onDrop}
              onClick={() => !file && fileRef.current?.click()}
              className={`relative border-2 border-dashed rounded-2xl transition-all duration-300 cursor-pointer overflow-hidden ${
                file
                  ? 'border-green-500/30 bg-green-500/[0.03]'
                  : dragOver
                    ? 'border-foreground/30 bg-foreground/[0.03] scale-[1.01]'
                    : 'border-border hover:border-foreground/20'
              }`}
              style={{ minHeight: file ? 'auto' : 220 }}
            >
              <input ref={fileRef} type="file" accept="video/*" className="hidden"
                onChange={e => { if (e.target.files?.[0]) handleFile(e.target.files[0]) }} />

              {file ? (
                <div className="p-6">
                  <div className="flex items-center gap-4">
                    {/* Video preview */}
                    {videoPreview && (
                      <div className="w-20 h-28 rounded-lg overflow-hidden bg-black shrink-0 shadow-md">
                        <video src={videoPreview} className="w-full h-full object-cover" muted />
                      </div>
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                        <span className="text-xs text-green-600/70 font-body font-medium">Ready to analyse</span>
                      </div>
                      <p className="font-medium text-sm text-foreground truncate">{file.name}</p>
                      <p className="text-xs text-foreground/30 font-body mt-0.5">{(file.size / 1024 / 1024).toFixed(1)} MB</p>
                    </div>
                    <button
                      onClick={e => { e.stopPropagation(); setFile(null); setVideoPreview(null) }}
                      className="px-3 py-1.5 text-[11px] rounded-lg border border-border text-foreground/30 hover:text-foreground/50 hover:border-foreground/20 transition-colors cursor-pointer font-body"
                    >
                      Change
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center py-16 px-6">
                  <div className={`w-14 h-14 rounded-2xl flex items-center justify-center mb-5 transition-all duration-300 ${
                    dragOver ? 'bg-foreground text-background scale-110' : 'bg-foreground/[0.04]'
                  }`}>
                    <svg className="w-7 h-7 text-foreground/30" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                    </svg>
                  </div>
                  <p className="font-display font-semibold text-base mb-1.5">
                    {dragOver ? 'Drop it here' : 'Drag & drop your video'}
                  </p>
                  <p className="text-foreground/25 text-sm font-body mb-4">or click to browse</p>
                  <div className="flex items-center gap-3 text-[10px] text-foreground/18 font-body">
                    <span>MP4, MOV, WEBM</span>
                    <span className="w-px h-3 bg-border" />
                    <span>Up to 500MB</span>
                    <span className="w-px h-3 bg-border" />
                    <span>Any resolution</span>
                  </div>
                </div>
              )}
            </div>

            {error && <p className="text-sm text-red-500 font-body text-center mt-4">{error}</p>}

            {/* Continue button — only shows after file is selected */}
            {file && (
              <button
                onClick={() => goTo(2)}
                className="w-full mt-6 py-4 rounded-2xl bg-foreground text-background font-display font-semibold text-[15px] cursor-pointer hover:opacity-90 shadow-lg shadow-foreground/10 transition-all"
                style={{ animation: 'fade-slide-in 0.3s ease-out' }}
              >
                Continue →
              </button>
            )}
          </div>
        )}

        {/* ═══════════════ STEP 2: NICHE ═══════════════ */}
        {step === 2 && (
          <div key="step2" className={slideClass}>
            <div className="text-center mb-10">
              <p className="text-[11px] font-semibold tracking-[0.2em] uppercase text-foreground/25 mb-4 font-body">Step 2</p>
              <h1 className="font-display text-4xl font-bold tracking-tight mb-3">
                What's your niche?
              </h1>
              <p className="text-foreground/40 font-body text-base">
                Pick up to 3 that describe your content
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
                onClick={() => {
                  if (selectedNiches.length === 0) { setError('Pick at least one niche'); return }
                  goTo(3)
                }}
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

        {/* ═══════════════ STEP 3: BRIEF + GOAL + SUBMIT ═══════════════ */}
        {step === 3 && (
          <div key="step3" className={slideClass}>
            <div className="text-center mb-8">
              <p className="text-[11px] font-semibold tracking-[0.2em] uppercase text-foreground/25 mb-4 font-body">Final step</p>
              <h1 className="font-display text-4xl font-bold tracking-tight mb-3">
                Tell us about it
              </h1>
              <p className="text-foreground/40 font-body text-base">
                Help Prysm understand your creative intent
              </p>
            </div>

            <textarea
              value={brief}
              onChange={e => { setBrief(e.target.value); setError('') }}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && brief.trim()) { e.preventDefault(); handleSubmit() } }}
              placeholder={"Describe what happens in the video — even a few words help.\ne.g. \"a short skit with my friends\" or \"talking to camera about a topic\""}
              rows={3}
              className="w-full border-2 border-border rounded-2xl px-5 py-4 text-sm text-foreground placeholder:text-foreground/18 outline-none focus:border-foreground/25 transition-all resize-none font-body bg-background"
            />
            <p className="text-[10px] text-foreground/20 font-body mt-1.5 text-center">This helps our AI understand your video better — it reads this during analysis</p>

            {error && (
              <div className="mt-4 px-4 py-3 rounded-xl bg-red-50/50 border border-red-200/50">
                <p className="text-red-600 text-sm font-body">{error}</p>
              </div>
            )}

            <div className="flex gap-3 mt-8">
              <button onClick={() => goTo(2)} className="flex-1 py-3.5 rounded-2xl border border-border text-foreground/40 text-sm font-body cursor-pointer hover:bg-secondary/50 transition-colors">
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
                Run Analysis →
              </button>
            </div>

            {/* Reassurance */}
            <div className="mt-8 flex items-center justify-center gap-6 text-[10px] text-foreground/20 font-body">
              <span>10 parameters</span>
              <span className="w-px h-3 bg-border" />
              <span>Trending hashtags</span>
              <span className="w-px h-3 bg-border" />
              <span>Caption rewrite</span>
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
                ? `Prysm analysed your video but your free credits ran out before the results could be unlocked. Upgrade to Pro to get more credits and see your results.`
                : `Prysm analysed your video but you ran out of credits mid-way. Your credits will renew next cycle — or upgrade to a higher plan to get more now.`}
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

      {/* ═══ UPGRADE MODAL — shown when credits exhausted before starting ═══ */}
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
