import { QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Toaster } from 'sonner'

import { TooltipProvider } from '@/components/ui/tooltip'

import { Devtools } from './devtools'
import { queryClient } from './queryClient'

/** Root providers. The router sits inside these (see main.tsx), so every route can use the query client and toasts. */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={200}>{children}</TooltipProvider>
      <Toaster position="bottom-right" richColors closeButton />
      <Devtools />
    </QueryClientProvider>
  )
}
