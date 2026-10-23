import { createFileRoute } from '@tanstack/react-router'

import { PageHeader } from '@/components/common/PageHeader'
import { requireAccess } from '@/features/auth'
import { DecisionCards } from '@/features/my-work'
import { NotificationsPanel } from '@/features/notifications'

export const Route = createFileRoute('/_app/my-work')({
  beforeLoad: requireAccess('myWork'),
  component: () => (
    <>
      <PageHeader title="My Work" description="What needs a decision from you, and what you have been told." />
      <div className="space-y-8">
        <DecisionCards />
        <NotificationsPanel />
      </div>
    </>
  ),
})
