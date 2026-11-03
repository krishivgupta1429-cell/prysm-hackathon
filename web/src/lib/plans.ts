/**
 * Plan gating utility — checks what features a user can access based on their plan.
 * Plans hierarchy: free < pro < plus < creator
 */

import { STUDIO_MODE } from './config'

export type PlanTier = 'free' | 'pro' | 'plus' | 'creator'

const PLAN_RANK: Record<PlanTier, number> = {
  free: 0,
  pro: 1,
  plus: 2,
  creator: 3,
}

export function getPlanRank(plan: string | undefined): number {
  return PLAN_RANK[(plan?.toLowerCase() || 'free') as PlanTier] ?? 0
}

/** True if user plan is at least the required tier */
export function hasPlan(userPlan: string | undefined, required: PlanTier): boolean {
  if (STUDIO_MODE) return true // the hackathon demo has no paid tiers: everything is unlocked
  return getPlanRank(userPlan) >= PLAN_RANK[required]
}

/** Feature access checks */
export const canAccess = {
  /** Captions, hashtags, trending audio suggestions */
  captionsAndHashtags: (plan: string | undefined) => hasPlan(plan, 'plus'),

  /** Analyse Instagram reels by pasting a link */
  linkAnalysis: (plan: string | undefined) => hasPlan(plan, 'plus'),

  /** Full dashboard with trends, comparison, coach */
  fullDashboard: (plan: string | undefined) => hasPlan(plan, 'plus'),

  /** Shareable analysis links */
  shareLinks: (plan: string | undefined) => hasPlan(plan, 'plus'),

  /** Credit rollovers */
  creditRollovers: (plan: string | undefined) => hasPlan(plan, 'plus'),

  /** Video Blueprint from competitor reels */
  videoBlueprint: (plan: string | undefined) => hasPlan(plan, 'plus'),

  /** Full analysis history (all saved) */
  fullHistory: (plan: string | undefined) => hasPlan(plan, 'plus'),

  /** More ARIA chats */
  moreAriaChats: (plan: string | undefined) => hasPlan(plan, 'plus'),

  /** Unlimited ARIA chats */
  unlimitedAriaChats: (plan: string | undefined) => hasPlan(plan, 'creator'),

  /** Audio energy curve on the results page */
  audioEnergyCurve: (plan: string | undefined) => hasPlan(plan, 'plus'),

  /** Predicted viewer behaviour (RunPod AI — Hook Hold Rate + Watch Rate) */
  predictedViewerBehaviour: (plan: string | undefined) => hasPlan(plan, 'plus'),
}

/** Monthly credit allocation per plan */
export const PLAN_CREDITS: Record<PlanTier, number> = {
  free: 20,
  pro: 100,  // Pro tier removed — maps to Plus credits as fallback
  plus: 100,
  creator: 400,
}

/** Get the plan's monthly credit allocation */
export function getPlanCredits(plan: string | undefined): number {
  return PLAN_CREDITS[(plan?.toLowerCase() || 'free') as PlanTier] ?? 20
}

/** Get the next upgrade plan */
export function getNextPlan(plan: string | undefined): { name: string; price: string } | null {
  const p = plan?.toLowerCase() || 'free'
  if (p === 'free') return { name: 'Pro', price: '₹799/mo' }
  if (p === 'pro' || p === 'plus') return { name: 'Creator', price: '₹2,499/mo' }
  return null
}

/** Get a human-readable plan label */
export function getPlanLabel(plan: string | undefined): string {
  const labels: Record<string, string> = { free: 'Free', pro: 'Pro', plus: 'Pro', creator: 'Creator' }
  return labels[plan?.toLowerCase() || 'free'] || 'Free'
}
