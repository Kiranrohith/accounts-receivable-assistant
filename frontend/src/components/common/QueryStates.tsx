import { AlertCircle, Inbox } from 'lucide-react'
import type { ReactNode } from 'react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'

interface ErrorAlertProps {
  error: unknown
  onRetry?: () => void
  title?: string
}

/** The inline failure state: the server's plain-language message and a Retry. */
export function ErrorAlert({ error, onRetry, title = 'Could not load this' }: ErrorAlertProps) {
  const message = error instanceof Error ? error.message : 'Something went wrong.'
  return (
    <Alert variant="destructive" role="alert">
      <AlertCircle />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p>{message}</p>
        {onRetry && (
          <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
            Retry
          </Button>
        )}
      </AlertDescription>
    </Alert>
  )
}

interface EmptyStateProps {
  /** One sentence saying what is missing. */
  message: string
  /** The one action that fills the list. */
  action?: ReactNode
}

export function EmptyState({ message, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
      <Inbox className="size-8 text-muted-foreground" aria-hidden />
      <p className="text-sm text-muted-foreground">{message}</p>
      {action}
    </div>
  )
}

/** A block of skeleton lines for panels that are not tables. */
export function SkeletonBlock({ lines = 4 }: { lines?: number }) {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="Loading">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className="h-5 w-full" />
      ))}
    </div>
  )
}
