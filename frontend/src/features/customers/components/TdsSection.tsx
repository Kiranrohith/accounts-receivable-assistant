import { useState } from 'react'
import { toast } from 'sonner'

import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { Button } from '@/components/ui/button'
import { useRefreshQueues } from '@/features/queues'

import { useRecomputeTds, useUpdateCustomer } from '../queries/customerQueries'
import type { CustomerDetail, TdsRecomputeResult } from '../types/customer'

interface TdsSectionProps {
  customer: CustomerDetail
  /** Whether this person may change TDS and recompute (customers.manage). */
  canManage: boolean
}

/**
 * TDS: whether the customer deducts it when paying. Changing the setting only shapes invoices created from now on;
 * "Recompute" brings the invoices already saved in line with it.
 */
export function TdsSection({ customer, canManage }: TdsSectionProps) {
  const update = useUpdateCustomer(customer.id)
  const recompute = useRecomputeTds(customer.id)
  const refreshQueues = useRefreshQueues()
  const [asking, setAsking] = useState<'flip' | 'recompute' | null>(null)
  const [result, setResult] = useState<TdsRecomputeResult | null>(null)
  const next = !customer.deducts_tds

  return (
    <section aria-label="TDS" className="space-y-2">
      <h3 className="text-sm font-semibold">TDS</h3>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={customer.deducts_tds} disabled={!canManage || update.isPending} onChange={() => setAsking('flip')} />
        This customer deducts TDS when paying
      </label>
      <p className="text-xs text-muted-foreground">A change applies to invoices created from now on. Invoices already saved keep their TDS until you recompute them.</p>

      {customer.tds_out_of_date > 0 && (
        <div role="status" className="space-y-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
          <p>
            {customer.tds_out_of_date === 1 ? '1 saved invoice still uses' : `${customer.tds_out_of_date} saved invoices still use`} the old TDS setting.
          </p>
          {canManage && (
            <Button size="sm" variant="outline" disabled={recompute.isPending} onClick={() => setAsking('recompute')}>
              Recompute open invoices
            </Button>
          )}
        </div>
      )}

      {result && (
        <div role="status" className="space-y-1 rounded-md border bg-muted/40 p-3 text-sm">
          <p>{result.message}</p>
          {result.skipped.length > 0 && (
            <ul className="list-disc pl-5 text-muted-foreground">
              {result.skipped.map((s) => (
                <li key={s.invoice_number}>
                  {s.invoice_number}: {s.reason}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <ConfirmDialog
        open={asking === 'flip'}
        onOpenChange={(open) => !open && setAsking(null)}
        title={next ? 'This customer deducts TDS' : 'This customer does not deduct TDS'}
        description="Invoices created from now on will follow this. Invoices already saved do not change until you recompute them."
        confirmLabel={next ? 'Yes, they deduct TDS' : 'Yes, they do not deduct TDS'}
        pending={update.isPending}
        onConfirm={() =>
          update.mutate(
            { deducts_tds: next },
            {
              onSuccess: () => {
                setAsking(null)
                setResult(null)
                toast.success('The TDS setting was changed.')
              },
              onError: () => setAsking(null),
            },
          )
        }
      />
      <ConfirmDialog
        open={asking === 'recompute'}
        onOpenChange={(open) => !open && setAsking(null)}
        title="Recompute open invoices"
        description="Open invoices are updated now as a new version. Part-paid or disputed ones wait for your decision in Take Action. Paid invoices and debit notes are left alone."
        confirmLabel="Recompute"
        pending={recompute.isPending}
        onConfirm={() =>
          recompute.mutate(undefined, {
            onSuccess: (done) => {
              setAsking(null)
              setResult(done)
              void refreshQueues() // decisions for part-paid or disputed invoices appear in Take Action
            },
            onError: () => setAsking(null),
          })
        }
      />
    </section>
  )
}
