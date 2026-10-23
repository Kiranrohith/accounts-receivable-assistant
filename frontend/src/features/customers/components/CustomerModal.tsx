import { useQuery } from '@tanstack/react-query'
import { Pencil } from 'lucide-react'
import { useState } from 'react'

import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { PERMISSIONS } from '@/config/constants'
import { hasPermission, useCurrentUser } from '@/features/auth'
import { formatCurrency, formatDate } from '@/utils/format'

import { customerQuery } from '../queries/customerQueries'
import type { CustomerDetail } from '../types/customer'
import { AliasesSection } from './AliasesSection'
import { EditCustomerForm } from './EditCustomerForm'
import { TdsSection } from './TdsSection'

interface CustomerModalProps {
  /** The customer the URL names; undefined when none is open. */
  customerId: string | undefined
  onClose: () => void
}

function Stats({ customer }: { customer: CustomerDetail }) {
  const tiles: [string, string][] = [
    ['Open invoices', String(customer.open_invoices)],
    ['Overdue', String(customer.overdue_invoices)],
    ['Outstanding', formatCurrency(customer.outstanding, 'INR')],
    ['Unapplied cash', formatCurrency(customer.unapplied_cash, 'INR')],
  ]
  return (
    <dl aria-label="Figures" className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {tiles.map(([label, value]) => (
        <div key={label} className="rounded-md border bg-muted/30 p-3">
          <dt className="text-xs text-muted-foreground">{label}</dt>
          <dd className="mt-0.5 font-semibold tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

function Details({ customer }: { customer: CustomerDetail }) {
  const rows: [string, string][] = [
    ['GSTIN', customer.gstin ?? '—'],
    ['Contact', customer.contact ?? '—'],
    ['E-mail', customer.email ?? '—'],
    ['Billing currency', customer.billing_currency ?? '—'],
    ['Country', customer.country ?? '—'],
    ['Added', formatDate(customer.created_at)],
  ]
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="font-medium">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

/**
 * A customer in a centred dialog (design section 12): figures, details (editable for customers.manage), confirmed aliases
 * and the TDS setting. A role that may only view sees everything and no edit control.
 */
export function CustomerModal({ customerId, onClose }: CustomerModalProps) {
  const user = useCurrentUser()
  const canManage = hasPermission(user, PERMISSIONS.customersManage)
  const canAlias = hasPermission(user, PERMISSIONS.customersAliasConfirm)
  const customer = useQuery({ ...customerQuery(customerId ?? ''), enabled: customerId !== undefined })
  const [editing, setEditing] = useState(false)

  return (
    <Dialog
      open={customerId !== undefined}
      onOpenChange={(open) => {
        if (!open) {
          setEditing(false)
          onClose()
        }
      }}
    >
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-[720px]">
        <DialogHeader>
          <DialogTitle>{customer.data?.customer_name ?? 'Customer'}</DialogTitle>
          <DialogDescription>Customer details</DialogDescription>
        </DialogHeader>
        {customer.isPending ? (
          <SkeletonBlock lines={6} />
        ) : customer.isError ? (
          <ErrorAlert error={customer.error} onRetry={() => customer.refetch()} />
        ) : (
          <div className="space-y-5">
            {customer.data.deducts_tds && <StatusChip status="TDS" label="Deducts TDS" />}
            <Stats customer={customer.data} />
            <section aria-label="Details" className="space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold">Details</h3>
                {canManage && !editing && (
                  <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
                    <Pencil /> Edit
                  </Button>
                )}
              </div>
              {editing ? <EditCustomerForm customer={customer.data} onDone={() => setEditing(false)} /> : <Details customer={customer.data} />}
            </section>
            <AliasesSection customer={customer.data} canAdd={canAlias} />
            <TdsSection customer={customer.data} canManage={canManage} />
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
