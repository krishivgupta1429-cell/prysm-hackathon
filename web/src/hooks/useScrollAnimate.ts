import { useEffect, useRef } from 'react'

/**
 * useScrollAnimate
 *
 * Uses IntersectionObserver to add/remove .visible on elements as they
 * enter/leave the viewport. Works seamlessly with Lenis because Lenis
 * dispatches real DOM scroll events that keep the IO thresholds correct.
 *
 * The rootMargin creates a small "entry buffer" so elements start
 * animating slightly before they reach the exact viewport edge.
 */
export function useScrollAnimate<T extends HTMLElement = HTMLDivElement>(
  options?: { threshold?: number; rootMargin?: string }
) {
  const ref = useRef<T>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          el.classList.add('visible')
        } else {
          el.classList.remove('visible')
        }
      },
      {
        threshold: options?.threshold ?? 0.1,
        rootMargin: options?.rootMargin ?? '0px 0px -60px 0px',
      }
    )

    observer.observe(el)
    return () => observer.disconnect()
  }, [options?.threshold, options?.rootMargin])

  return ref
}
