import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ApiError } from '@/lib/axios'

function describe(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong.'
}

interface ClientOptions {
  /** Tests turn retrying off so a failure shows at once. */
  retry?: boolean
}

/**
 * One place for cache defaults and for telling the user about failures.
 * A failed query already shows its own inline alert with Retry, so only background refetch failures toast;
 * every failed mutation toasts (the dialog that started it stays open for another try).
 */
export function createQueryClient({ retry = true }: ClientOptions = {}) {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        if (query.state.data !== undefined) toast.error(describe(error))
      },
    }),
    mutationCache: new MutationCache({
      onError: (error, _vars, _ctx, mutation) => {
        if (mutation.options.onError) return // the caller handles it
        toast.error(describe(error))
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: retry ? (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2 : false,
        refetchOnWindowFocus: false,
      },
    },
  })
}

export const queryClient = createQueryClient()
