import { PageHeader } from '@/components/common/PageHeader'
import { PERMISSIONS } from '@/config/constants'
import { hasPermission, useCurrentUser } from '@/features/auth'

import { DisputesSection } from './DisputesSection'
import { ReceivablesSection } from './ReceivablesSection'

/**
 * Where the money stands and how the complaints are going. Each part is shown only to a role that may see its figures: the
 * receivables to anyone who can view invoices, the disputes to anyone who can view the dispute dashboard.
 */
export function DashboardPage() {
  const user = useCurrentUser()
  const showReceivables = hasPermission(user, PERMISSIONS.invoicesView)
  const showDisputes = hasPermission(user, PERMISSIONS.disputesDashboardView)

  return (
    <>
      <PageHeader title="Dashboard" description="Where the money stands and how the complaints are going." />
      <div className="space-y-8">
        {showReceivables && (
          <div className="space-y-3">
            <h2 className="text-base font-semibold">Receivables</h2>
            <ReceivablesSection />
          </div>
        )}
        {showDisputes && (
          <div className="space-y-3">
            <h2 className="text-base font-semibold">Disputes</h2>
            <DisputesSection />
          </div>
        )}
        {!showReceivables && !showDisputes && <p className="text-sm text-muted-foreground">Your role has no figures to show here. Your decisions are in My Work.</p>}
      </div>
    </>
  )
}
