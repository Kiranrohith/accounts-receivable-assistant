import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { EvidenceLayout } from '@/components/common/EvidenceModal'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { ReasonDialog } from '@/components/common/ReasonDialog'
import { Button } from '@/components/ui/button'
import { formatCurrency } from '@/utils/format'

import { tdsReviewQuery, useApproveTds, useRejectTds } from '../queries/tdsReviewQueries'
import type { TdsReview } from '../services/tdsReviewService'
import { DecidedNotice, ReasonsBox } from './parts/ReasonsBox'
import type { PanelProps } from './types'

/** "TDS changes": you asked to recompute TDS and this invoice is part paid or disputed. See old against new, then apply or leave it. */
export function TdsReviewPanel({ itemId, onDone }: PanelProps) {
  const review = useQuery(tdsReviewQuery(itemId))

  if (review.isPending) return <EvidenceLayout left={<SkeletonBlock lines={6} />} right={<SkeletonBlock lines={6} />} />
  if (review.isError) return <div className="p-4"><ErrorAlert error={review.error} onRetry={() => review.refetch()} /></div>
  return <TdsBody review={review.data} itemId={itemId} onDone={onDone} />
}

function Comparison({ review }: { review: TdsReview }) {
  const rows: [string, string, string][] = [
    ['TDS deducted', review.old_tds, review.new_tds],
    ['Amount to collect', review.old_collectible, review.new_collectible],
  ]
  return (
    <div className="space-y-4 p-4">
      <div>
        <h3 className="font-semibold">Invoice {review.invoice_number ?? ''}</h3>
        <p className="text-sm text-muted-foreground">
          {review.customer_name ?? 'Customer'} {review.deducts_tds ? 'now deducts TDS' : 'no longer deducts TDS'}.
        </p>
      </div>
      <table className="w-full text-sm" aria-label="TDS comparison">
        <thead>
          <tr className="text-left text-muted-foreground">
            <th className="py-1 font-normal">Figure</th>
            <th className="py-1 text-right font-normal">Now</th>
            <th className="py-1 text-right font-normal">After the change</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, before, after]) => (
            <tr key={label} className="border-t">
              <th scope="row" className="py-1 text-left font-medium">{label}</th>
              <td className="py-1 text-right text-muted-foreground">{formatCurrency(before, review.currency)}</td>
              <td className="py-1 text-right font-medium">{formatCurrency(after, review.currency)}</td>
            </tr>
          ))}
          <tr className="border-t">
            <th scope="row" className="py-1 text-left font-medium">Already paid</th>
            <td className="py-1 text-right" colSpan={2}>{formatCurrency(review.amount_paid, review.currency)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

function TdsBody({ review, itemId, onDone }: { review: TdsReview; itemId: string; onDone: PanelProps['onDone'] }) {
  const approve = useApproveTds(itemId)
  const reject = useRejectTds(itemId)
  const [asking, setAsking] = useState<'approve' | 'reject' | null>(null)
  const number = review.invoice_number ?? 'the invoice'
  const busy = approve.isPending || reject.isPending

  return (
    <>
      <EvidenceLayout
        left={<Comparison review={review} />}
        right={
          <div className="space-y-5">
            <ReasonsBox title="Why it needs your decision" reasons={review.reasons} />
            {review.status !== 'Pending' ? (
              <DecidedNotice status={review.status} reason={review.decision_reason} />
            ) : (
              <div className="flex flex-col gap-2">
                <Button disabled={busy} onClick={() => setAsking('approve')}>
                  Apply the new TDS
                </Button>
                <Button variant="outline" disabled={busy} onClick={() => setAsking('reject')}>
                  Leave the invoice as it is
                </Button>
              </div>
            )}
          </div>
        }
      />
      <ReasonDialog
        open={asking === 'approve'}
        onOpenChange={(open) => !open && setAsking(null)}
        title="Apply the new TDS"
        description={`${number} is updated as a new version; its old state is kept. Say why you are applying it.`}
        confirmLabel="Confirm and apply"
        pending={approve.isPending}
        onConfirm={(reason) =>
          approve.mutate(reason, {
            onSuccess: (decision) => {
              setAsking(null)
              onDone(decision.version === null ? `${number} now carries the new TDS.` : `${number} now carries the new TDS (version ${decision.version}).`)
            },
            onError: () => setAsking(null),
          })
        }
      />
      <ReasonDialog
        open={asking === 'reject'}
        onOpenChange={(open) => !open && setAsking(null)}
        title="Leave the invoice as it is"
        description="Nothing changes on the invoice. You can recompute again later."
        confirmLabel="Leave it"
        pending={reject.isPending}
        onConfirm={(reason) =>
          reject.mutate(reason, {
            onSuccess: () => {
              setAsking(null)
              onDone(`${number} was left as it is.`)
            },
            onError: () => setAsking(null),
          })
        }
      />
    </>
  )
}
