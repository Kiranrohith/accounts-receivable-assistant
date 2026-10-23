import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { ErrorAlert, EmptyState, SkeletonBlock } from '@/components/common/QueryStates'
import { Button } from '@/components/ui/button'
import { DEFAULT_PAGE_SIZE } from '@/config/constants'

import { notificationsPageQuery, unreadCountQuery, useMarkAllNotificationsRead, useMarkNotificationRead } from '../queries/notificationQueries'
import { NotificationRow } from './NotificationRow'

/** My Work's notifications: every one of the person's notices, a page at a time, unread in bold. */
export function NotificationsPanel() {
  const [page, setPage] = useState(1)
  const list = useQuery(notificationsPageQuery(page, DEFAULT_PAGE_SIZE))
  const unread = useQuery(unreadCountQuery())
  const markRead = useMarkNotificationRead()
  const markAll = useMarkAllNotificationsRead()
  const lastPage = Math.max(1, Math.ceil((list.data?.total ?? 0) / DEFAULT_PAGE_SIZE))

  return (
    <section id="notifications" aria-labelledby="notifications-title" className="rounded-lg border bg-card">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <h2 id="notifications-title" className="text-base font-semibold">
          Notifications
        </h2>
        <Button variant="outline" size="sm" disabled={!unread.data || markAll.isPending} onClick={() => markAll.mutate()}>
          Mark all read
        </Button>
      </div>

      {list.isPending ? (
        <div className="p-4">
          <SkeletonBlock lines={5} />
        </div>
      ) : list.isError ? (
        <div className="p-4">
          <ErrorAlert error={list.error} onRetry={() => list.refetch()} />
        </div>
      ) : list.data.items.length === 0 ? (
        <EmptyState message="Nothing yet. Assignments, deadlines and failures show up here." />
      ) : (
        <>
          <ul>
            {list.data.items.map((n) => (
              <li key={n.notification_id} className="border-b last:border-0">
                <NotificationRow notification={n} onOpen={(item) => !item.is_read && markRead.mutate(item.notification_id)} />
              </li>
            ))}
          </ul>
          <div className="flex items-center justify-between border-t px-4 py-2 text-sm text-muted-foreground">
            <span>
              Page {page} of {lastPage} · {list.data.total} in all
            </span>
            <div className="flex gap-1">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                Previous
              </Button>
              <Button variant="outline" size="sm" disabled={page >= lastPage} onClick={() => setPage(page + 1)}>
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </section>
  )
}
