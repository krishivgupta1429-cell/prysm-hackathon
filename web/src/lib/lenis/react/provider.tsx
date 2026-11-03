import { Lenis } from '../core/lenis'
import type { ScrollCallback } from '../core/types'
import {
  createContext,
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react'
import { Store } from './store'
import type { LenisContextValue, LenisProps, LenisRef } from './types'

export const LenisContext = createContext<LenisContextValue | null>(null)

export const rootLenisContextStore = new Store<LenisContextValue | null>(null)

export const ReactLenis = forwardRef<LenisRef, LenisProps>(
  (
    {
      children,
      root = false,
      options = {},
      autoRaf = true,
      className = '',
      ...props
    }: LenisProps,
    ref
  ) => {
    const wrapperRef = useRef<HTMLDivElement>(null)
    const contentRef = useRef<HTMLDivElement>(null)
    const [lenis, setLenis] = useState<Lenis | undefined>(undefined)

    useImperativeHandle(
      ref,
      () => ({
        wrapper: wrapperRef.current,
        content: contentRef.current,
        lenis,
      }),
      [lenis]
    )

    useEffect(() => {
      const lenisInstance = new Lenis({
        ...options,
        ...(wrapperRef.current &&
          contentRef.current && {
            wrapper: wrapperRef.current!,
            content: contentRef.current!,
          }),
        autoRaf: options?.autoRaf ?? autoRaf,
      })

      setLenis(lenisInstance)

      return () => {
        lenisInstance.destroy()
        setLenis(undefined)
      }
    }, [autoRaf, JSON.stringify({ ...options, wrapper: null, content: null })])

    const callbacksRefs = useRef<{ callback: ScrollCallback; priority: number }[]>([])

    const addCallback: LenisContextValue['addCallback'] = useCallback(
      (callback, priority) => {
        callbacksRefs.current.push({ callback, priority })
        callbacksRefs.current.sort((a, b) => a.priority - b.priority)
      },
      []
    )

    const removeCallback: LenisContextValue['removeCallback'] = useCallback(
      (callback) => {
        callbacksRefs.current = callbacksRefs.current.filter(
          (cb) => cb.callback !== callback
        )
      },
      []
    )

    useEffect(() => {
      if (root && lenis) {
        rootLenisContextStore.set({ lenis, addCallback, removeCallback })
        return () => rootLenisContextStore.set(null)
      }
    }, [root, lenis, addCallback, removeCallback])

    useEffect(() => {
      if (!lenis) return

      const onScroll: ScrollCallback = (data) => {
        for (const { callback } of callbacksRefs.current) {
          callback(data)
        }
      }

      lenis.on('scroll', onScroll)
      return () => { lenis.off('scroll', onScroll) }
    }, [lenis])

    if (!children) return null

    return (
      <LenisContext.Provider value={{ lenis: lenis!, addCallback, removeCallback }}>
        {root && root !== 'asChild' ? (
          children
        ) : (
          <div
            ref={wrapperRef}
            className={`${className} ${lenis?.className ?? ''}`.trim()}
            {...props}
          >
            <div ref={contentRef}>{children}</div>
          </div>
        )}
      </LenisContext.Provider>
    )
  }
)

ReactLenis.displayName = 'ReactLenis'
