// Built during the Meta Global AI Developer Hackathon (new work).
// Deployment-specific values, all read from VITE_* variables so nothing about a particular deployment is hardcoded.
// With none of them set the site behaves exactly as before (full pricing and billing UI).
const env = import.meta.env

/** VITE_STUDIO_MODE=1 turns the site into the hackathon demo: no pricing, billing, plans, credits or upgrade prompts (the brand stays "Prysm"). */
export const STUDIO_MODE: boolean = env.VITE_STUDIO_MODE === '1'

/** Reel-link analysis: on in the regular site, hidden in demo mode unless VITE_STUDIO_ALLOW_LINKS=1 (the backend flag is STUDIO_ALLOW_LINKS). */
export const ALLOW_LINKS: boolean = !STUDIO_MODE || env.VITE_STUDIO_ALLOW_LINKS === '1'

/** Contact address for mailto links. A reserved example address unless VITE_SUPPORT_EMAIL is set. */
export const SUPPORT_EMAIL: string = env.VITE_SUPPORT_EMAIL || 'support@example.com'

/** Social profile links; a link is only shown when its variable is set. */
export const SOCIAL_LINKS = {
  x: (env.VITE_SOCIAL_X as string | undefined) || '',
  linkedin: (env.VITE_SOCIAL_LINKEDIN as string | undefined) || '',
  instagram: (env.VITE_SOCIAL_INSTAGRAM as string | undefined) || '',
}

/** VITE_SHOW_COST_PANEL=1 (local testing) shows the per-analysis cost panel on the results page. It is also shown to the logged-in user whose
 *  email equals VITE_FOUNDER_EMAIL. The backend still decides: it answers 404 for anyone who may not see the numbers. */
export const SHOW_COST_PANEL: boolean = env.VITE_SHOW_COST_PANEL === '1'
export const FOUNDER_EMAIL: string = ((env.VITE_FOUNDER_EMAIL as string | undefined) || '').trim().toLowerCase()
