import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { getToken, getUser, clearAuth, fetchUser, User } from '@/lib/auth'
import { apiUrl } from '@/lib/api'
import { getPlanLabel, getPlanCredits, getNextPlan } from '@/lib/plans'
import { STUDIO_MODE } from '@/lib/config'

interface UsageEvent {
  id: string
  type: 'analysis' | 'chat'
  label: string
  detail: string
  niche: string
  score: number | null
  source_type: string | null
  credits: number
  created_at: string
}

interface UsageData {
  plan: string
  monthly_credits: number
  credits_remaining: number
  credits_consumed: number
  cycle_start: string
  plan_expires_at: string | null
  events: UsageEvent[]
}

function formatDate(iso: string) {
  const d = new Date(iso)
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
}

function daysUntil(iso: string | null) {
  if (!iso) return null
  const diff = new Date(iso).getTime() - Date.now()
  return Math.max(0, Math.ceil(diff / (1000 * 60 * 60 * 24)))
}

export default function Settings() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<'account' | 'usage' | 'billing'>('account')
  const [editingHandle, setEditingHandle] = useState(false)
  const [handleValue, setHandleValue] = useState('')
  const [handleError, setHandleError] = useState('')
  const [savingHandle, setSavingHandle] = useState(false)
  const [igStatus, setIgStatus] = useState<'idle' | 'connected' | 'error'>('idle')
  const [usageData, setUsageData] = useState<UsageData | null>(null)
  const [usageLoading, setUsageLoading] = useState(false)
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()

  useEffect(() => {
    const token = getToken()
    if (!token) { navigate('/login'); return }
    const cached = getUser()
    if (cached) { setUser(cached); setLoading(false) }
    fetchUser().then(u => {
      if (!u) { navigate('/login'); return }
      setUser(u)
      setLoading(false)
    })
    const ig = searchParams.get('instagram')
    if (ig === 'connected') setIgStatus('connected')
    else if (ig === 'error') setIgStatus('error')
    const tab = searchParams.get('tab')
    if (!STUDIO_MODE && (tab === 'usage' || tab === 'billing')) setActiveTab(tab as any)
  }, [navigate])

  useEffect(() => {
    if (activeTab !== 'usage' || usageData) return
    setUsageLoading(true)
    fetch(apiUrl('/billing/usage'), {
      headers: { Authorization: `Bearer ${getToken()}` },
    })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setUsageData(d) })
      .finally(() => setUsageLoading(false))
  }, [activeTab])

  function handleSignOut() {
    clearAuth()
    navigate('/')
  }

  async function handleSaveHandle() {
    const cleaned = handleValue.replace(/^@/, '').trim()
    if (!cleaned || cleaned.length < 3) { setHandleError('Handle must be at least 3 characters'); return }
    if (!/^[a-zA-Z0-9._]+$/.test(cleaned)) { setHandleError('Only letters, numbers, dots, and underscores'); return }
    setHandleError('')
    setSavingHandle(true)
    try {
      const res = await fetch(apiUrl('/auth/update-handle'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify({ username: cleaned.toLowerCase() }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Failed to update handle')
      const updated = await fetchUser()
      if (updated) setUser(updated)
      setEditingHandle(false)
    } catch (err: any) {
      setHandleError(err.message || 'Something went wrong')
    } finally {
      setSavingHandle(false)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="w-7 h-7 border-2 border-foreground/10 border-t-foreground/40 rounded-full animate-spin" />
      </div>
    )
  }

  const planCredits = getPlanCredits(user?.plan)
  const creditsRemaining = Math.round((user?.credits || 0) * 10) / 10
  const creditsConsumed = Math.max(0, Math.round((planCredits - creditsRemaining) * 10) / 10)
  const pct = Math.min((creditsRemaining / Math.max(planCredits, creditsRemaining || 1)) * 100, 100)
  const isLowCredits = creditsRemaining < 10

  const planAccent =
    user?.plan === 'creator' ? { bar: 'from-amber-500 to-green-500', text: 'text-green-700', bg: 'bg-green-50/60', border: 'border-green-200/50', badge: 'bg-green-100 text-green-700' } :
    user?.plan === 'plus'    ? { bar: 'from-amber-500 to-green-500', text: 'text-green-700', bg: 'bg-green-50/60', border: 'border-green-200/50', badge: 'bg-green-100 text-green-700' } :
    user?.plan === 'pro'     ? { bar: 'from-amber-500 to-green-500', text: 'text-green-700', bg: 'bg-green-50/60', border: 'border-green-200/50', badge: 'bg-green-100 text-green-700' } :
                               { bar: 'from-foreground/30 to-foreground/15', text: 'text-foreground/50', bg: 'bg-foreground/[0.03]', border: 'border-border', badge: 'bg-foreground/[0.06] text-foreground/45' }

  return (
    <div className="min-h-screen bg-background text-foreground relative">
      <div className="grain-overlay" aria-hidden="true" />

      {/* Nav */}
      <nav className="border-b border-border px-4 sm:px-8 h-14 flex items-center justify-between sticky top-0 bg-background/90 backdrop-blur-md z-50">
        <a href="/" className="font-logo text-[18px] sm:text-[21px] text-foreground">PRYSM</a>
        <a href="/dashboard" className="text-sm text-foreground/40 hover:text-foreground/60 transition-colors font-body">
          ← Back to dashboard
        </a>
      </nav>

      <div className="max-w-2xl mx-auto px-5 py-10">

        {/* Header */}
        <div className="mb-8">
          <h1 className="font-display text-3xl font-black tracking-tight mb-1">Settings</h1>
          <p className="text-foreground/35 text-sm font-body">{STUDIO_MODE ? 'Manage your account' : 'Manage your account and subscription'}</p>
        </div>

        {/* Tabs (demo mode has no usage or billing, so no tabs) */}
        {!STUDIO_MODE && <div className="flex gap-0 mb-8 border-b border-border">
          {(['account', 'usage', 'billing'] as const).map(tab => (
            <button key={tab} onClick={() => setActiveTab(tab)}
              className="relative px-5 py-2.5 text-[13px] font-semibold font-body capitalize transition-all cursor-pointer"
              style={{
                color: activeTab === tab ? 'var(--color-foreground, #1a1a1a)' : undefined,
                marginBottom: -1,
              }}>
              <span className={activeTab === tab ? 'text-foreground' : 'text-foreground/35 hover:text-foreground/55'}>
                {tab}
              </span>
              {activeTab === tab && (
                <span className="absolute bottom-0 left-0 right-0 h-[2px] bg-foreground rounded-t-full" />
              )}
            </button>
          ))}
        </div>}

        {/* ══════════════ ACCOUNT TAB ══════════════ */}
        {activeTab === 'account' && (
          <div className="space-y-5">

            {!STUDIO_MODE && (<>
            {/* Plan card */}
            <div className={`rounded-2xl border ${planAccent.border} ${planAccent.bg} p-5`}>
              <div className="flex items-start justify-between mb-4">
                <div>
                  <p className="text-[10px] font-bold tracking-widest uppercase text-foreground/30 mb-1 font-body">Current plan</p>
                  <div className="flex items-center gap-2.5">
                    <p className="font-display text-2xl font-black tracking-tight">{getPlanLabel(user?.plan)}</p>
                    <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full font-body ${planAccent.badge}`}>
                      {user?.plan === 'free' ? 'Free tier' : 'Active'}
                    </span>
                  </div>
                </div>
                <div className="text-right">
                  <p className="text-[10px] font-body text-foreground/30 mb-0.5">Credits remaining</p>
                  <p className={`font-display text-2xl font-black tracking-tight ${isLowCredits ? 'text-amber-600' : 'text-green-700'}`}>
                    {creditsRemaining}
                  </p>
                  <p className="text-[11px] text-foreground/30 font-body">of {planCredits}</p>
                </div>
              </div>

              {/* Progress bar */}
              <div className="w-full h-1.5 bg-foreground/[0.07] rounded-full overflow-hidden mb-4">
                <div className={`h-full rounded-full bg-gradient-to-r transition-all duration-700 ${isLowCredits ? 'from-amber-500 to-amber-400' : planAccent.bar}`}
                  style={{ width: `${pct}%` }} />
              </div>

              <div className="flex items-center justify-between">
                <p className="text-[11px] text-foreground/35 font-body">
                  {creditsConsumed} credits used this cycle
                </p>
                {!STUDIO_MODE && getNextPlan(user?.plan) && (
                  <a href="/pricing"
                    className="text-[12px] font-body font-bold text-foreground/60 hover:text-foreground transition-colors border border-border rounded-lg px-3 py-1.5 hover:border-foreground/25 hover:bg-foreground/[0.03] transition-all">
                    Upgrade plan →
                  </a>
                )}
              </div>
            </div>

            </>)}

            {/* Account info */}
            <div>
              <p className="text-[10px] font-bold tracking-widest uppercase text-foreground/25 mb-3 font-body">Account</p>
              <div className="border border-border rounded-xl overflow-hidden bg-background">
                <div className="flex justify-between items-center px-5 py-3.5">
                  <span className="text-sm text-foreground/40 font-body">Email</span>
                  <span className="text-sm text-foreground/70 font-body">{user?.email}</span>
                </div>

                <div className="h-px bg-border" />

                <div className="flex justify-between items-center px-5 py-3.5">
                  <span className="text-sm text-foreground/40 font-body">Creator Handle</span>
                  {editingHandle ? (
                    <div className="flex items-center gap-2">
                      <div className="relative">
                        <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-xs text-foreground/30 font-body">@</span>
                        <input
                          type="text"
                          value={handleValue}
                          onChange={e => { setHandleValue(e.target.value.replace(/[^a-zA-Z0-9._]/g, '').toLowerCase()); setHandleError('') }}
                          placeholder="your_handle"
                          autoFocus
                          className="border border-border rounded-lg pl-6 pr-3 py-1.5 text-sm text-foreground placeholder:text-foreground/20 outline-none focus:border-foreground/25 transition-all font-body bg-background w-36"
                          onKeyDown={e => { if (e.key === 'Enter') handleSaveHandle(); if (e.key === 'Escape') { setEditingHandle(false); setHandleError('') } }}
                        />
                      </div>
                      <button onClick={handleSaveHandle} disabled={savingHandle}
                        className="text-xs text-foreground/60 hover:text-foreground/80 transition-colors cursor-pointer font-body font-semibold disabled:opacity-40">
                        {savingHandle ? '...' : 'Save'}
                      </button>
                      <button onClick={() => { setEditingHandle(false); setHandleError('') }}
                        className="text-xs text-foreground/30 hover:text-foreground/50 transition-colors cursor-pointer font-body">
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-foreground/70 font-body">@{user?.username}</span>
                      <button onClick={() => { setHandleValue(user?.username || ''); setEditingHandle(true) }}
                        className="text-[11px] text-foreground/30 hover:text-foreground/55 transition-colors cursor-pointer font-body border border-border rounded-md px-2 py-0.5 hover:border-foreground/20">
                        Edit
                      </button>
                    </div>
                  )}
                </div>
                {handleError && <p className="text-red-500 text-xs font-body text-right px-5 pb-3 -mt-1">{handleError}</p>}

                {!STUDIO_MODE && (<>
                <div className="h-px bg-border" />

                <div className="flex justify-between items-center px-5 py-3.5">
                  <span className="text-sm text-foreground/40 font-body">Plan</span>
                  <span className={`text-sm font-semibold font-body ${planAccent.text}`}>{getPlanLabel(user?.plan)}</span>
                </div>
                </>)}
              </div>
            </div>

            {/* Instagram connect + Creator DNA (hackathon): the flow lives on /creator-dna */}
            <div>
              <p className="text-[10px] font-bold tracking-widest uppercase text-foreground/25 mb-3 font-body">Integrations</p>
              <div className="border border-border rounded-xl p-5 bg-background">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-display font-semibold">Instagram and Creator DNA</p>
                    <p className="text-[12px] text-foreground/35 font-body mt-0.5">
                      {igStatus === 'connected' ? 'Your Instagram account is connected.' : 'Connect your own account to personalise every analysis with your real reels.'}
                    </p>
                    {igStatus === 'error' && <p className="text-[12px] text-red-500/70 font-body mt-1">Connection failed. Please try again.</p>}
                  </div>
                  <a href="/creator-dna"
                    className="px-4 py-2 rounded-xl bg-foreground text-background text-[12px] font-body font-bold hover:opacity-85 transition-opacity whitespace-nowrap">
                    {igStatus === 'connected' ? 'Open →' : 'Connect →'}
                  </a>
                </div>
              </div>
            </div>

            <div className="pt-1">
              <button onClick={handleSignOut}
                className="text-sm text-foreground/30 hover:text-foreground/55 transition-colors font-body cursor-pointer">
                Sign out
              </button>
            </div>
          </div>
        )}

        {/* ══════════════ USAGE TAB ══════════════ */}
        {activeTab === 'usage' && (
          <div className="space-y-5">
            {usageLoading ? (
              <div className="flex items-center justify-center py-20">
                <div className="w-7 h-7 border-2 border-foreground/10 border-t-foreground/40 rounded-full animate-spin" />
              </div>
            ) : usageData ? (
              <>
                {/* Summary card */}
                <div className={`rounded-2xl border ${planAccent.border} ${planAccent.bg} p-5`}>
                  <div className="flex items-start justify-between mb-5">
                    <div>
                      <p className="text-[10px] font-bold tracking-widest uppercase text-foreground/30 mb-1 font-body">Current Plan</p>
                      <p className="font-display text-2xl font-black tracking-tight">{getPlanLabel(usageData.plan)}</p>
                    </div>
                    <div className="text-right">
                      <p className="text-[10px] font-body text-foreground/30 mb-1 uppercase tracking-wider">
                        {usageData.plan_expires_at ? 'Resets in' : 'Cycle started'}
                      </p>
                      <p className="text-sm font-semibold font-body text-foreground/70">
                        {usageData.plan_expires_at
                          ? `${daysUntil(usageData.plan_expires_at)} days`
                          : formatDate(usageData.cycle_start)}
                      </p>
                    </div>
                  </div>

                  <div className="mb-4">
                    <div className="flex justify-between items-baseline mb-2">
                      <span className="text-[12px] text-foreground/40 font-body">Credits consumed this cycle</span>
                      <span className="text-sm font-display font-bold text-foreground">
                        {usageData.credits_consumed}
                        <span className="text-foreground/30 font-body font-normal text-xs"> / {usageData.monthly_credits || '∞'}</span>
                      </span>
                    </div>
                    {usageData.monthly_credits > 0 && (
                      <div className="w-full h-1.5 bg-foreground/[0.07] rounded-full overflow-hidden">
                        <div className={`h-full rounded-full bg-gradient-to-r transition-all duration-700 ${
                          usageData.credits_consumed / usageData.monthly_credits > 0.8
                            ? 'from-amber-500 to-amber-400'
                            : planAccent.bar
                        }`}
                          style={{ width: `${Math.min((usageData.credits_consumed / usageData.monthly_credits) * 100, 100)}%` }} />
                      </div>
                    )}
                  </div>

                  <div className="flex items-center gap-5 pt-3 border-t border-foreground/[0.07]">
                    <div>
                      <p className="text-[10px] text-foreground/25 font-body uppercase tracking-wider mb-0.5">Remaining</p>
                      <p className="text-lg font-display font-black tracking-tight text-green-700">{usageData.credits_remaining}</p>
                    </div>
                    <div className="h-7 w-px bg-border" />
                    <div>
                      <p className="text-[10px] text-foreground/25 font-body uppercase tracking-wider mb-0.5">Cycle start</p>
                      <p className="text-sm font-display font-bold">{formatDate(usageData.cycle_start)}</p>
                    </div>
                    {usageData.plan_expires_at && (
                      <>
                        <div className="h-7 w-px bg-border" />
                        <div>
                          <p className="text-[10px] text-foreground/25 font-body uppercase tracking-wider mb-0.5">Renews</p>
                          <p className="text-sm font-display font-bold">{formatDate(usageData.plan_expires_at)}</p>
                        </div>
                      </>
                    )}
                  </div>
                </div>

                {/* Credit log */}
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <p className="text-[10px] font-bold tracking-widest uppercase text-foreground/25 font-body">Credit Log</p>
                    <p className="text-[11px] text-foreground/25 font-body">{usageData.events.length} event{usageData.events.length !== 1 ? 's' : ''} this cycle</p>
                  </div>

                  {usageData.events.length === 0 ? (
                    <div className="border border-border rounded-xl p-10 text-center bg-background">
                      <p className="text-foreground/25 text-sm font-body">No credit usage this cycle yet</p>
                    </div>
                  ) : (
                    <div className="border border-border rounded-xl overflow-hidden bg-background">
                      <div className="grid grid-cols-[1fr_auto_auto] gap-4 px-5 py-2.5 border-b border-border bg-foreground/[0.02]">
                        <span className="text-[10px] font-bold tracking-widest uppercase text-foreground/25 font-body">Event</span>
                        <span className="text-[10px] font-bold tracking-widest uppercase text-foreground/25 font-body text-right w-24">Date</span>
                        <span className="text-[10px] font-bold tracking-widest uppercase text-foreground/25 font-body text-right w-16">Credits</span>
                      </div>
                      {usageData.events.map((ev, i) => {
                        const isAnalysis = ev.type === 'analysis'
                        const isLink = ev.source_type === 'link'
                        const badgeCls = isAnalysis
                          ? (isLink
                            ? 'bg-green-100 text-green-700'
                            : 'bg-indigo-100 text-indigo-700')
                          : 'bg-violet-100 text-violet-700'

                        return (
                          <div key={ev.id}
                            className={`grid grid-cols-[1fr_auto_auto] gap-4 px-5 py-3.5 items-center hover:bg-foreground/[0.015] transition-colors ${i < usageData.events.length - 1 ? 'border-b border-border/60' : ''}`}>
                            <div className="min-w-0">
                              <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                                <span className={`inline-flex items-center gap-1 text-[10px] font-bold tracking-wider uppercase px-2 py-0.5 rounded-md font-body ${badgeCls}`}>
                                  {isAnalysis ? (isLink ? '🔗' : '⬆') : '✦'} {ev.label}
                                </span>
                                {ev.score != null && (
                                  <span className="text-[10px] text-foreground/30 font-body">Score {ev.score}</span>
                                )}
                              </div>
                              <p className="text-[12px] text-foreground/40 font-body truncate">
                                {ev.niche || ev.detail?.slice(0, 60) || '—'}
                              </p>
                            </div>
                            <span className="text-[12px] text-foreground/35 font-body text-right w-24 shrink-0">
                              {formatDate(ev.created_at)}
                            </span>
                            <span className="text-[13px] font-display font-black text-red-500/80 text-right w-16 shrink-0">
                              −{ev.credits}
                            </span>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div className="text-center py-20">
                <p className="text-foreground/30 text-sm font-body">Could not load usage data</p>
              </div>
            )}
          </div>
        )}

        {/* ══════════════ BILLING TAB ══════════════ */}
        {activeTab === 'billing' && (
          <div className="space-y-5">
            {/* Plan card */}
            <div className={`rounded-2xl border ${planAccent.border} ${planAccent.bg} p-5`}>
              <div className="flex items-start justify-between mb-5">
                <div>
                  <p className="text-[10px] font-bold tracking-widest uppercase text-foreground/30 mb-1 font-body">Your plan</p>
                  <p className="font-display text-2xl font-black tracking-tight">{getPlanLabel(user?.plan)}</p>
                </div>
                {getNextPlan(user?.plan) && (
                  <a href="/pricing"
                    className="px-4 py-2 rounded-xl bg-foreground text-background text-[12px] font-body font-bold hover:opacity-85 transition-opacity">
                    Upgrade →
                  </a>
                )}
              </div>

              {/* Credit bar */}
              <div className="mb-4">
                <div className="flex justify-between items-baseline mb-2">
                  <span className="text-[12px] text-foreground/40 font-body">Credits remaining</span>
                  <span className="text-sm font-display font-bold">{creditsRemaining} <span className="text-foreground/30 font-normal font-body text-xs">/ {planCredits}</span></span>
                </div>
                <div className="w-full h-1.5 bg-foreground/[0.07] rounded-full overflow-hidden">
                  <div className={`h-full rounded-full bg-gradient-to-r transition-all duration-700 ${isLowCredits ? 'from-amber-500 to-amber-400' : planAccent.bar}`}
                    style={{ width: `${pct}%` }} />
                </div>
              </div>

              <div className="pt-3 border-t border-foreground/[0.07] flex flex-col gap-2.5">
                {user?.plan && user.plan !== 'free' && (
                  <div className="flex justify-between items-center">
                    <span className="text-[12px] text-foreground/35 font-body">Plan renews</span>
                    <span className="text-[12px] text-foreground/60 font-body">
                      {usageData?.plan_expires_at ? formatDate(usageData.plan_expires_at) : '—'}
                    </span>
                  </div>
                )}
                <div className="flex justify-between items-center">
                  <span className="text-[12px] text-foreground/35 font-body">Credits consumed this cycle</span>
                  <span className="text-[12px] font-semibold font-body text-red-500/70">{creditsConsumed}</span>
                </div>
              </div>
            </div>

            <button onClick={() => setActiveTab('usage')}
              className="text-sm text-foreground/40 hover:text-foreground/65 transition-colors font-body cursor-pointer underline underline-offset-2">
              View full credit log →
            </button>
          </div>
        )}

      </div>
    </div>
  )
}
