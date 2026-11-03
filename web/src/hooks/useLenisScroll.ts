import { useLenis } from '@/lib/lenis/react'
import type { ScrollCallback } from '@/lib/lenis/core/types'

/**
 * useLenisScroll — thin wrapper around useLenis for convenience.
 *
 * @example
 *   useLenisScroll((lenis) => {
 *     console.log(lenis.progress) // 0 → 1
 *   })
 */
export function useLenisScroll(callback: ScrollCallback, deps: unknown[] = []) {
  return useLenis(callback, deps)
}
