// Built during the Meta Global AI Developer Hackathon (new work).
// Count-up for key numbers. Shows the final number at once when the visitor prefers reduced motion or the browser cannot say.
import { useEffect, useState } from 'react'

export const prefersReducedMotion = () =>
  import.meta.env.MODE === 'test' || typeof window === 'undefined' || typeof window.matchMedia !== 'function' || window.matchMedia('(prefers-reduced-motion: reduce)').matches

/** `value` counted up from 0 to its target over `ms`; null stays null. */
export function useCountUp(value: number | null | undefined, ms = 700): number | null {
  const [shown, setShown] = useState<number | null>(value == null || prefersReducedMotion() ? (value ?? null) : 0)
  useEffect(() => {
    if (value == null) { setShown(null); return }
    if (prefersReducedMotion()) { setShown(value); return }
    let raf = 0
    const t0 = performance.now()
    const tick = (t: number) => {
      const k = Math.min(1, (t - t0) / ms)
      setShown(value * (1 - Math.pow(1 - k, 3)))
      if (k < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [value, ms])
  return shown
}
