import { Link } from '@tanstack/react-router'

import { cn } from '@/lib/utils'
import { formatAge } from '@/utils/format'

import type { Notification } from '../services/notificationService'
import { notificationLabel, notificationTarget } from '../utils/target'

interface NotificationRowProps {
  notification: Notification
  /** Called when the row is opened (so the caller can mark it read). */
  onOpen: (n: Notification) => void
}

/** One notice: unread bold; a link to its item when there is a screen for it, a button that just marks it read when not. */
export function NotificationRow({ notification: n, onOpen }: NotificationRowProps) {
  const target = notificationTarget(n)
  const classes = 'flex w-full flex-col gap-0.5 px-4 py-2.5 text-left text-sm hover:bg-muted/50'
  const body = (
    <>
      <span className="flex items-center gap-2">
        <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground">{notificationLabel(n.type)}</span>
        <span className="text-xs text-muted-foreground">{formatAge(n.created_at)}</span>
        {!n.is_read && <span className="ml-auto size-2 rounded-full bg-primary" aria-label="Unread" />}
      </span>
      <span className={cn(!n.is_read && 'font-semibold')}>{n.message ?? n.type}</span>
    </>
  )

  return target ? (
    <Link {...target} className={classes} onClick={() => onOpen(n)}>
      {body}
    </Link>
  ) : (
    <button type="button" className={classes} onClick={() => onOpen(n)}>
      {body}
    </button>
  )
}
