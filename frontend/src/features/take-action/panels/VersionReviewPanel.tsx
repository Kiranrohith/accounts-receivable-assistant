import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { DocumentViewer } from '@/components/common/DocumentViewer'
import { EvidenceLayout } from '@/components/common/EvidenceModal'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { ReasonDialog } from '@/components/common/ReasonDialog'
import { Button } from '@/components/ui/button'

import { useApproveVersion, useRejectVersion, versionReviewQuery } from '../queries/versionReviewQueries'
import type { VersionReview } from '../services/versionReviewService'
import { describeVersionApproval, showValue, versionCurrency } from '../utils/versionChanges'
import { DecidedNotice, ReasonsBox } from './parts/ReasonsBox'
import type { PanelProps } from './types'

/** "Version review": a new copy of an invoice already saved. See what differs, then apply it or turn it down. */
export function VersionReviewPanel({ itemId, onDone }: PanelProps) {
  const review = useQuery(versionReviewQuery(itemId))

  if (review.isPending) return <EvidenceLayout left={<SkeletonBlock lines={8} />} right={<SkeletonBlock lines={8} />} />
  if (review.isError) return <div className="p-4"><ErrorAlert error={review.error} onRetry={() => review.refetch()} /></div>
  return <VersionReviewBody review={review.data} itemId={itemId} onDone={onDone} />
}

function VersionReviewBody({ review, itemId, onDone }: { review: VersionReview; itemId: string; onDone: PanelProps['onDone'] }) {
  const approve = useApproveVersion(itemId)
  const reject = useRejectVersion(itemId)
  const [asking, setAsking] = useState<'approve' | 'reject' | null>(null)
  const busy = approve.isPending || reject.isPending
  const decided = review.status !== 'Pending'
  const currency = versionCurrency(review.proposed)
  const number = review.invoice_number ?? 'the invoice'

  return (
    <>
      <EvidenceLayout
        left={review.source_document_id ? <DocumentViewer documentId={review.source_document_id} fileName={review.file_name} /> : null}
        right={
          <div className="space-y-5">
            <div>
              <h3 className="font-semibold">Invoice {review.invoice_number ?? ''} has a new copy</h3>
              <p className="text-sm text-muted-foreground">
                {review.file_name ?? 'The uploaded document'} is compared with saved version {review.base_version}.
              </p>
            </div>

            <ReasonsBox title="Why it needs your decision" reasons={review.reasons} />

            <section aria-label="Changes" className="space-y-1">
              <h4 className="text-sm font-medium">What changed</h4>
              {review.changes.length === 0 ? (
                <p className="text-sm text-muted-foreground">No field differs from the saved invoice.</p>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-muted-foreground">
                      <th className="py-1 font-normal">Field</th>
                      <th className="py-1 font-normal">Saved</th>
                      <th className="py-1 font-normal">New copy</th>
                    </tr>
                  </thead>
                  <tbody>
                    {review.changes.map((change) => (
                      <tr key={change.field} className="border-t align-top">
                        <th scope="row" className="py-1 pr-2 text-left font-medium">{change.label}</th>
                        <td className="py-1 pr-2 text-muted-foreground">
                          {showValue(change, change.old, currency).map((line) => <div key={line}>{line}</div>)}
                        </td>
                        <td className="py-1">
                          {showValue(change, change.new, currency).map((line) => <div key={line} className="font-medium">{line}</div>)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>

            {decided ? (
              <DecidedNotice status={review.status} reason={review.decision_reason} />
            ) : (
              <div className="flex flex-col gap-2">
                {review.can_approve ? (
                  <Button disabled={busy} onClick={() => setAsking('approve')}>
                    Apply the new version
                  </Button>
                ) : (
                  <p className="rounded-md border bg-muted/40 p-3 text-sm">This invoice is already paid, so the new copy cannot be applied. It can only be rejected.</p>
                )}
                <Button variant="destructive" disabled={busy} onClick={() => setAsking('reject')}>
                  Reject this copy
                </Button>
              </div>
            )}
          </div>
        }
      />

      <ReasonDialog
        open={asking === 'approve'}
        onOpenChange={(open) => !open && setAsking(null)}
        title="Apply the new version"
        description={`The saved invoice ${number} is kept as an earlier version and updated with the new copy.`}
        confirmLabel="Confirm and apply"
        pending={approve.isPending}
        onConfirm={(reason) =>
          approve.mutate(reason, {
            onSuccess: (decision) => {
              setAsking(null)
              onDone(describeVersionApproval(decision, review.invoice_number))
            },
            onError: () => setAsking(null),
          })
        }
      />
      <ReasonDialog
        open={asking === 'reject'}
        onOpenChange={(open) => !open && setAsking(null)}
        title="Reject this copy"
        description="The saved invoice is left as it is. The new document moves to the rejected list with your reason."
        confirmLabel="Reject copy"
        pending={reject.isPending}
        onConfirm={(reason) =>
          reject.mutate(reason, {
            onSuccess: () => {
              setAsking(null)
              onDone(`The new copy of ${number} was rejected.`)
            },
            onError: () => setAsking(null),
          })
        }
      />
    </>
  )
}
