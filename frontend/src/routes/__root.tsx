import type { QueryClient } from '@tanstack/react-query'
import { createRootRouteWithContext, Link, Outlet } from '@tanstack/react-router'
import { Suspense } from 'react'

import { RouterDevtools } from '@/app/devtools'
import { Button } from '@/components/ui/button'
import { env } from '@/config/env'

interface RouterContext {
  queryClient: QueryClient
}

export const Route = createRootRouteWithContext<RouterContext>()({
  component: () => (
    <>
      <Outlet />
      {env.devtools && (
        <Suspense fallback={null}>
          <RouterDevtools />
        </Suspense>
      )}
    </>
  ),
  // The root error boundary: a render crash anywhere shows this instead of a blank page.
  errorComponent: ({ error, reset }) => (
    <div className="mx-auto mt-24 max-w-md space-y-4 text-center" role="alert">
      <h1 className="text-xl font-semibold">Something broke on this page</h1>
      <p className="text-sm text-muted-foreground">{error instanceof Error ? error.message : 'Unexpected error.'}</p>
      <Button onClick={reset}>Try again</Button>
    </div>
  ),
  notFoundComponent: () => (
    <div className="mx-auto mt-24 max-w-md space-y-4 text-center">
      <h1 className="text-xl font-semibold">Page not found</h1>
      <p className="text-sm text-muted-foreground">There is nothing at this address.</p>
      <Button asChild>
        <Link to="/dashboard">Go to the dashboard</Link>
      </Button>
    </div>
  ),
})
