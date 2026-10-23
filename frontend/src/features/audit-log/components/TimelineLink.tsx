import { Link } from '@tanstack/react-router'
import { History } from 'lucide-react'

import { PERMISSIONS } from '@/config/constants'
import { hasPermission, useCurrentUser } from '@/features/auth'

/** "Timeline" on a record's page: its state log, in the order it happened. Shown only to someone who may read that log. */
export function TimelineLink({ entityType, entityId }: { entityType: string; entityId: string }) {
  const user = useCurrentUser()
  if (!hasPermission(user, PERMISSIONS.logsTimelineView)) return null
  return (
    <Link
      to="/audit-log"
      search={{ tab: 'state', entityType, entityId, dir: 'asc', page: 1, size: 25 }}
      className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
    >
      <History className="size-4" aria-hidden /> Timeline
    </Link>
  )
}
