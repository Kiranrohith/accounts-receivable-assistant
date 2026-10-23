import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderResult } from '@testing-library/react'
import type { ReactElement } from 'react'

import { TooltipProvider } from '@/components/ui/tooltip'

/** Renders with a fresh query client per test, so no test sees another's cached data. */
export function renderWithClient(ui: ReactElement): RenderResult & { client: QueryClient } {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } })
  const result = render(
    <QueryClientProvider client={client}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  )
  return { ...result, client }
}
