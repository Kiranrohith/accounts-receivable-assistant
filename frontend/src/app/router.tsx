import { createRouter } from '@tanstack/react-router'

import { Skeleton } from '@/components/ui/skeleton'
import { authStore, setToken, signOut } from '@/features/auth/store/authStore'
import { connectAuth } from '@/lib/axios'

import { routeTree } from '../routeTree.gen'
import { queryClient } from './queryClient'

export const router = createRouter({
  routeTree,
  context: { queryClient },
  defaultPreload: 'intent',
  // Matches the query cache's staleTime, so preloading on hover is not repeated within 30 s.
  defaultPreloadStaleTime: 30_000,
  defaultPendingComponent: () => (
    <div className="space-y-3 p-6">
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-64 w-full" />
    </div>
  ),
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

// The axios layer cannot import the router, so the router hands it what to do when a session ends.
connectAuth({
  getToken: () => authStore.state.token,
  onToken: setToken,
  onUnauthorized: () => {
    signOut()
    queryClient.clear()
    void router.navigate({ to: '/login', search: { redirect: router.state.location.href } })
  },
})
