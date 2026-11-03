import { useState } from 'react'
import { useScrollAnimate } from '@/hooks/useScrollAnimate'
import { Check, Sparkles, ArrowRight, CheckCircle2 } from 'lucide-react'
import { isLoggedIn, getUser } from '@/lib/auth'
import type { User } from '@/lib/auth'
import { subscribeToPlan } from '@/lib/razorpay'
import { hasPlan } from '@/lib/plans'

const plans = [
  {
    name: 'Free',
    tagline: 'See what Prysm can do',
    monthlyINR: 0,
    annualINR: 0,
    monthlyUSD: 0,
    annualUSD: 0,
    credits: 20,
    oneTime: true,
    cta: 'Get started',
    ctaHref: '/register',
    popular: false,
    tierIntro: null,
    features: [
      '20 one-time credits',
      'Limited parameter access (2 of 10)',
      'Limited step-by-step fixes',
      'Fewer ARIA AI chats',
      'No analysis history on dashboard',
    ],
  },
  {
    name: 'Pro',
    planId: 'plus',   // internal backend/DB key — do not change
    tagline: 'Analyse more, post better',
    monthlyINR: 799,
    annualINR: 600,
    annualTotalINR: 7199,
    monthlyUSD: 9.99,
    annualUSD: 7.99,
    annualTotalUSD: 95.88,
    credits: 100,
    oneTime: false,
    cta: 'Get Pro',
    ctaHref: '/register',
    popular: true,
    tierIntro: 'All features in Free, plus:',
    features: [
      '100 monthly credits',
      'Full 10-parameter analysis with step-by-step fixes',
      'Full dashboard with trends & comparison',
      'Suggested hashtags, captions & trending audio',
      'Audio energy curve on every analysis',
      'Video Blueprint from any reel you analyse',
      'Predicted viewer behaviour (AI)',
    ],
  },
  {
    name: 'Creator',
    tagline: 'Scale your content strategy',
    monthlyINR: 2499,
    annualINR: 1667,
    annualTotalINR: 19999,
    monthlyUSD: 29.99,
    annualUSD: 24.99,
    annualTotalUSD: 299.88,
    credits: 400,
    oneTime: false,
    cta: 'Get Creator',
    ctaHref: '/register',
    popular: false,
    tierIntro: 'All features in Pro, plus:',
    features: [
      '400 monthly credits',
      'Unlimited ARIA AI chats',
      'Early access to new features',
      'Priority email support',
    ],
  },
]

function detectIsIndia(): boolean {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''
    if (tz.startsWith('Asia/Kolkata') || tz.startsWith('Asia/Calcutta')) return true
    const locale = navigator.language || ''
    if (locale.startsWith('hi') || locale === 'en-IN') return true
  } catch {}
  return false
}

const Pricing = ({ hideFreePlan = false, verifiedUser }: { hideFreePlan?: boolean; verifiedUser?: User | null }) => {
  const [annual, setAnnual] = useState(false)
  const currency = detectIsIndia() ? 'INR' : 'USD'
  const [paymentStatus, setPaymentStatus] = useState<'idle' | 'processing' | 'success' | 'error'>('idle')
  const [paymentMessage, setPaymentMessage] = useState('')
  const titleRef = useScrollAnimate<HTMLDivElement>()

  const sym = currency === 'INR' ? '₹' : '$'
  const loggedIn = isLoggedIn()
  // Use verifiedUser (from backend) if provided, else fall back to localStorage
  const user = verifiedUser !== undefined ? verifiedUser : getUser()

  function handlePlanClick(planKey: string) {
    if (planKey === 'free') {
      window.location.href = '/register'
      return
    }
    if (!loggedIn) {
      window.location.href = '/register'
      return
    }
    // Already on this plan or higher
    if (hasPlan(user?.plan, planKey as any)) {
      const displayName = planKey === 'plus' ? 'Pro' : planKey.charAt(0).toUpperCase() + planKey.slice(1)
      setPaymentMessage(`You're already on the ${displayName} plan or higher.`)
      setPaymentStatus('error')
      setTimeout(() => setPaymentStatus('idle'), 3000)
      return
    }

    setPaymentStatus('processing')
    subscribeToPlan({
      plan: planKey as 'pro' | 'plus' | 'creator',
      cycle: annual ? 'annual' : 'monthly',
      onSuccess: (data) => {
        setPaymentStatus('success')
        const planDisplayName: Record<string, string> = { plus: 'Pro', creator: 'Creator', pro: 'Pro' }
        const displayName = planDisplayName[data.plan] ?? (data.plan.charAt(0).toUpperCase() + data.plan.slice(1))
        setPaymentMessage(`Welcome to ${displayName}! You now have ${data.credits} credits.`)
        setTimeout(() => {
          setPaymentStatus('idle')
          window.location.reload()
        }, 3000)
      },
      onError: (msg) => {
        setPaymentStatus('error')
        setPaymentMessage(msg)
        setTimeout(() => setPaymentStatus('idle'), 4000)
      },
    })
  }

  return (
    <section id="pricing" className="border-t border-border relative overflow-hidden">
      {/* Subtle background glow behind pricing */}
      <div className="absolute inset-0 pointer-events-none">
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] sm:w-[900px] h-[400px] sm:h-[600px] rounded-full opacity-[0.07]"
          style={{ background: 'radial-gradient(circle, hsl(44 60% 70%) 0%, transparent 70%)' }} />
      </div>

      <div className="relative max-w-6xl mx-auto px-4 sm:px-6 py-16 sm:py-24">
        {/* Header */}
        <div ref={titleRef} className="scroll-animate mb-10 sm:mb-14 text-center">
          <span className="tag-chip mb-5 inline-flex">Pricing</span>
          <h2 className="font-display text-2xl sm:text-4xl md:text-5xl font-bold text-foreground tracking-tight leading-[1.1]">
            Simple, transparent
            <br />
            <span className="text-foreground/30">pricing</span>
          </h2>
          <p className="mt-4 text-foreground/50 font-body max-w-[52ch] mx-auto leading-relaxed">
            Start free — upgrade when you're ready. Every plan includes our full 10-parameter AI analysis.
          </p>
        </div>

        {/* Toggles */}
        <div className="flex flex-wrap items-center justify-center gap-3 sm:gap-4 mb-8 sm:mb-10">
          {/* Billing toggle */}
          <div className="flex items-center gap-0.5 bg-foreground/[0.04] rounded-xl p-1">
            <button
              onClick={() => setAnnual(false)}
              className={`text-[13px] font-body font-semibold px-4 py-2 rounded-lg transition-all duration-200 ${
                !annual ? 'text-foreground bg-background shadow-sm border border-border/60' : 'text-foreground/40 hover:text-foreground/60'
              }`}
            >
              Monthly
            </button>
            <button
              onClick={() => setAnnual(true)}
              className={`text-[13px] font-body font-semibold px-4 py-2 rounded-lg transition-all duration-200 ${
                annual ? 'text-foreground bg-background shadow-sm border border-border/60' : 'text-foreground/40 hover:text-foreground/60'
              }`}
            >
              Annual
              <span className="ml-1.5 text-[10px] font-bold text-green-600 bg-green-100/60 px-1.5 py-0.5 rounded-md">-17%</span>
            </button>
          </div>

        </div>

        {/* Plan cards */}
        <div className={`grid grid-cols-1 gap-3 sm:gap-4 pt-5 ${hideFreePlan ? 'sm:grid-cols-2 max-w-3xl mx-auto' : 'sm:grid-cols-2 md:grid-cols-3'}`}>
          {plans.filter(p => !(hideFreePlan && p.name === 'Free')).map((plan, i) => {
            const price = plan.oneTime
              ? 0
              : annual
                ? (currency === 'INR' ? plan.annualINR : plan.annualUSD)
                : (currency === 'INR' ? plan.monthlyINR : plan.monthlyUSD)

            const totalAnnual = annual && !plan.oneTime
              ? (currency === 'INR' ? plan.annualTotalINR : plan.annualTotalUSD)
              : null

            return (
              <div
                key={plan.name}
                className={`group relative rounded-2xl border p-5 sm:p-6 flex flex-col transition-all duration-300 hover:-translate-y-1 ${
                  plan.popular
                    ? 'border-foreground/40 bg-foreground text-background shadow-xl shadow-foreground/10'
                    : 'border-border bg-background hover:border-foreground/20 hover:shadow-lg hover:shadow-foreground/[0.04]'
                }`}
                style={{ opacity: 0, animation: `fade-slide-in 0.5s ease-out ${i * 80}ms forwards` }}
              >
                {/* Popular badge — sits above the plan name, takes up space so all cards align */}
                <div className="h-7 flex items-center justify-center mb-3">
                  {plan.popular && (
                    <span className="inline-flex items-center gap-1.5 text-[10px] font-bold tracking-widest uppercase px-3.5 py-1.5 rounded-full bg-background text-foreground shadow-md border border-border/60">
                      <Sparkles className="w-3 h-3" />
                      Most Popular
                    </span>
                  )}
                </div>

                {/* Plan name + tagline */}
                <div className="mb-5">
                  <h3 className={`font-display text-xl font-bold ${plan.popular ? 'text-background' : 'text-foreground'}`}>{plan.name}</h3>
                  <p className={`text-[13px] font-body mt-1 ${plan.popular ? 'text-background/50' : 'text-foreground/40'}`}>{plan.tagline}</p>
                </div>

                {/* Price */}
                <div className="mb-6">
                  <div className="flex items-baseline gap-1.5">
                    <span className={`font-display text-4xl font-black tracking-tight ${plan.popular ? 'text-background' : 'text-foreground'}`}>
                      {plan.oneTime ? `${sym}0` : `${sym}${currency === 'INR' ? Math.round(price as number).toLocaleString('en-IN') : price}`}
                    </span>
                    <span className={`text-[12px] font-body leading-tight ${plan.popular ? 'text-background/40' : 'text-foreground/35'}`}>
                      {currency === 'INR' ? 'INR' : 'USD'} /<br />month
                    </span>
                  </div>
                  {totalAnnual ? (
                    <p className={`text-[11px] font-body mt-1.5 ${plan.popular ? 'text-background/35' : 'text-foreground/30'}`}>
                      {sym}{currency === 'INR' ? Math.round(totalAnnual as number).toLocaleString('en-IN') : totalAnnual} billed annually
                    </p>
                  ) : (
                    <div className="h-4" /> /* spacer for alignment */
                  )}
                </div>

                {/* CTA */}
                {(() => {
                  // planId is the backend key ('free', 'plus', 'creator')
                  // plan.name is the display label ('Free', 'Pro', 'Creator')
                  const planId = (plan as any).planId ?? plan.name.toLowerCase()
                  // Show "Current plan" on the exact plan the user is on.
                  // For logged-in users with no plan set, default to 'free'.
                  const effectivePlan = loggedIn ? (user?.plan?.toLowerCase() || 'free') : null
                  const isExactCurrentPlan = effectivePlan === planId
                  return isExactCurrentPlan ? (
                    <div className={`flex items-center justify-center gap-2 w-full py-3 rounded-xl text-[13.5px] font-body mb-6 ${
                      plan.popular ? 'text-background/50' : 'text-foreground/30'
                    }`}>
                      <CheckCircle2 className="w-4 h-4" />
                      Current plan
                    </div>
                  ) : (
                    <button
                      onClick={() => handlePlanClick(planId)}
                      disabled={paymentStatus === 'processing'}
                      className={`group/btn flex items-center justify-center gap-2 w-full text-center py-3 rounded-xl text-[13.5px] font-bold font-body transition-all duration-200 mb-6 cursor-pointer disabled:opacity-50 ${
                        plan.popular
                          ? 'bg-background text-foreground hover:bg-background/90 shadow-sm'
                          : 'border border-border text-foreground/70 hover:bg-foreground hover:text-background hover:border-foreground'
                      }`}
                    >
                      {paymentStatus === 'processing' ? (
                        <div className="w-4 h-4 border-2 border-current/20 border-t-current rounded-full animate-spin" />
                      ) : (
                        <>
                          {plan.cta}
                          <ArrowRight className="w-3.5 h-3.5 opacity-0 -translate-x-1 group-hover/btn:opacity-100 group-hover/btn:translate-x-0 transition-all duration-200" />
                        </>
                      )}
                    </button>
                  )
                })()}

                {/* Divider */}
                <div className={`h-px mb-5 ${plan.popular ? 'bg-background/15' : 'bg-border'}`} />

                {/* Tier intro */}
                {plan.tierIntro && (
                  <p className={`text-[12px] font-display font-bold mb-3 ${plan.popular ? 'text-background/70' : 'text-foreground/70'}`}>{plan.tierIntro}</p>
                )}

                {/* Features */}
                <ul className="space-y-3 flex-1">
                  {plan.features.map((f) => (
                    <li key={f} className={`flex items-start gap-2.5 text-[13px] font-body ${plan.popular ? 'text-background/65' : 'text-foreground/55'}`}>
                      <div className={`w-4.5 h-4.5 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5 ${
                        plan.popular ? 'bg-background/15' : 'bg-green-100/60'
                      }`} style={{ width: 18, height: 18 }}>
                        <Check className={`w-2.5 h-2.5 ${plan.popular ? 'text-background/70' : 'text-green-600/70'}`} />
                      </div>
                      {f}
                    </li>
                  ))}
                </ul>
              </div>
            )
          })}
        </div>

        {/* Bottom note */}
        <p className="mt-10 text-center text-[13px] font-body text-foreground/30">
          Free includes 2 parameters. Pro and Creator unlock all 10 with full step-by-step fixes.
        </p>
      </div>

      {/* Payment toast */}
      {paymentStatus !== 'idle' && paymentStatus !== 'processing' && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[100]"
          style={{ animation: 'fade-slide-in 0.3s ease-out' }}>
          <div className={`px-5 py-3 rounded-xl shadow-xl border font-body text-[13px] font-medium ${
            paymentStatus === 'success'
              ? 'bg-green-50 border-green-200/60 text-green-700'
              : 'bg-red-50 border-red-200/60 text-red-700'
          }`}>
            {paymentMessage}
          </div>
        </div>
      )}
    </section>
  )
}
export default Pricing
