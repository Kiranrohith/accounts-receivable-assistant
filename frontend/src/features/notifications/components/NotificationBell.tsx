import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Bell } from 'lucide-react'

import { CountBadge } from '@/components/common/CountBadge'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

import { notificationsPageQuery, unreadCountQuery, useMarkAllNotificationsRead, useMarkNotificationRead } from '../queries/notificationQueries'
import { NotificationRow } from './NotificationRow'

/** The top-bar bell: the unread count, and the latest ten in a dropdown. "View all" opens My Work's panel. */
export function NotificationBell() {
  const unread = useQuery(unreadCountQuery())
  const latest = useQuery(notificationsPageQuery(1, 10))
  const markRead = useMarkNotificationRead()
  const markAll = useMarkAllNotificationsRead()

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="relative" aria-label={`Notifications, ${unread.data ?? 0} unread`}>
          <Bell />
          <CountBadge count={unread.data ?? 0} corner />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-96 p-0">
        <div className="flex items-center justify-between border-b px-4 py-2.5">
          <h2 className="text-sm font-semibold">Notifications</h2>
          <Button variant="link" size="sm" disabled={!unread.data || markAll.isPending} onClick={() => markAll.mutate()}>
            Mark all read
          </Button>
        </div>
        <div className="max-h-96 overflow-y-auto">
          {latest.isPending ? (
            <div className="p-4">
              <SkeletonBlock lines={3} />
            </div>
          ) : latest.isError ? (
            <div className="p-4">
              <ErrorAlert error={latest.error} onRetry={() => latest.refetch()} />
            </div>
          ) : latest.data.items.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-muted-foreground">You are all caught up.</p>
          ) : (
            <ul>
              {latest.data.items.map((n) => (
                <li key={n.notification_id} className="border-b last:border-0">
                  <NotificationRow notification={n} onOpen={(item) => !item.is_read && markRead.mutate(item.notification_id)} />
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="border-t px-4 py-2 text-right">
          <Button asChild variant="link" size="sm">
            <Link to="/my-work" hash="notifications">
              View all
            </Link>
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
