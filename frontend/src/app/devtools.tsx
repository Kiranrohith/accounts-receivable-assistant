import { lazy, Suspense } from 'react'

import { env } from '@/config/env'

// Referencing import.meta.env.DEV directly lets the production build drop both devtools bundles entirely.
const QueryDevtools = import.meta.env.DEV
  ? lazy(() => import('@tanstack/react-query-devtools').then((m) => ({ default: () => <m.ReactQueryDevtools buttonPosition="bottom-left" /> })))
  : () => null

export const RouterDevtools = import.meta.env.DEV
  ? lazy(() => import('@tanstack/react-router-devtools').then((m) => ({ default: () => <m.TanStackRouterDevtools position="bottom-right" /> })))
  : () => null

/** Off unless VITE_DEVTOOLS=true in a development build; renders nothing otherwise. */
export function Devtools() {
  if (!env.devtools) return null
  return (
    <Suspense fallback={null}>
      <QueryDevtools />
    </Suspense>
  )
}
