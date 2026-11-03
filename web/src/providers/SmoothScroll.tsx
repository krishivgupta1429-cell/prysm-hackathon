import { ReactLenis, useLenis } from '@/lib/lenis/react'
import type { ReactNode } from 'react'
import { useEffect } from 'react'

/**
 * Inner component — has access to the Lenis context so it can
 * subscribe to the custom 'lenis:scrollTo' event dispatched by the Navbar.
 */
function LenisScrollToListener() {
  const lenis = useLenis()

  useEffect(() => {
    if (!lenis) return

    const handler = (e: Event) => {
      const { target, offset = 0 } = (e as CustomEvent).detail as {
        target: HTMLElement | string | number
        offset?: number
      }
      lenis.scrollTo(target, { offset, duration: 1.2, easing: (t) => Math.min(1, 1.001 - 2 ** (-10 * t)) })
    }

    window.addEventListener('lenis:scrollTo', handler)
    return () => window.removeEventListener('lenis:scrollTo', handler)
  }, [lenis])

  return null
}

/**
 * SmoothScroll — root Lenis provider.
 *
 * Options:
 *  lerp 0.08  → silky smooth, not sluggish
 *  smoothWheel true  → interpolates wheel events
 *  syncTouch false   → native touch inertia on mobile
 */
export default function SmoothScroll({ children }: { children: ReactNode }) {
  return (
    <ReactLenis
      root
      options={{
        lerp: 0.08,
        smoothWheel: true,
        syncTouch: false,
        wheelMultiplier: 0.9,
        touchMultiplier: 1.5,
      }}
    >
      <LenisScrollToListener />
      {children}
    </ReactLenis>
  )
}
