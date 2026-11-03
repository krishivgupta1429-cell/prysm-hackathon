import type { ScrollCallback } from '../core/types'
import { useContext, useEffect } from 'react'
import { LenisContext, rootLenisContextStore } from './provider'
import { useStore } from './store'
import type { LenisContextValue } from './types'

const fallbackContext: Partial<LenisContextValue> = {}

export function useLenis(
  callback?: ScrollCallback,
  deps: unknown[] = [],
  priority = 0
) {
  const localContext = useContext(LenisContext)
  const rootContext  = useStore(rootLenisContextStore)
  const currentContext = localContext ?? rootContext ?? fallbackContext

  const { lenis, addCallback, removeCallback } = currentContext

  useEffect(() => {
    if (!(callback && addCallback && removeCallback && lenis)) return

    addCallback(callback, priority)
    callback(lenis)

    return () => { removeCallback(callback) }
  }, [lenis, addCallback, removeCallback, priority, ...deps, callback])

  return lenis
}
