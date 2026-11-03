import type Lenis from '../core/lenis'
import type { LenisOptions, ScrollCallback } from '../core/types'
import type { ComponentPropsWithoutRef, ReactNode } from 'react'

export type LenisContextValue = {
  lenis: Lenis
  addCallback: (callback: ScrollCallback, priority: number) => void
  removeCallback: (callback: ScrollCallback) => void
}

export type LenisProps = ComponentPropsWithoutRef<'div'> & {
  root?: boolean | 'asChild'
  options?: LenisOptions
  autoRaf?: boolean
  children?: ReactNode
  className?: string | undefined
}

export type LenisRef = {
  wrapper: HTMLDivElement | null
  content: HTMLDivElement | null
  lenis?: Lenis
}
