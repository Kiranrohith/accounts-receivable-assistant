import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { DocumentViewer } from '@/components/common/DocumentViewer'
import { EvidenceLayout } from '@/components/common/EvidenceModal'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { ReasonDialog } from '@/components/common/ReasonDialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

import { poReviewQuery, useApprovePo, useRejectPo } from '../queries/poReviewQueries'
import type { PoReview } from '../services/poReviewService'
import { LOW_CONFIDENCE } from '../utils/invoiceEdits'
import type { LineForm } from '../utils/lines'
import { describePoApproval, diffPoEdits, PO_FIELDS, poLinesOf, toPoForm, type PoFormValues } from '../utils/poEdits'
import { CustomerPicker } from './parts/CustomerPicker'
import { LineEditor } from './parts/LineEditor'
import { DecidedNotice, ReasonsBox } from './parts/ReasonsBox'
import type { PanelProps } from './types'

/** "Purchase order check": the reader took these values from a purchase order. Correct them, then approve or reject. */
export function PoReviewPanel({ itemId, onDone }: PanelProps) {
  const review = useQuery(poReviewQuery(itemId))

  if (review.isPending) return <EvidenceLayout left={<SkeletonBlock lines={8} />} right={<SkeletonBlock lines={10} />} />
  if (review.isError) return <div className="p-4"><ErrorAlert error={review.error} onRetry={() => review.refetch()} /></div>
  return <PoReviewForm review={review.data} itemId={itemId} onDone={onDone} />
}

function PoReviewForm({ review, itemId, onDone }: { review: PoReview; itemId: string; onDone: PanelProps['onDone'] }) {
  const approve = useApprovePo(itemId)
  const reject = useRejectPo(itemId)

  const extraction = review.extraction
  const [form, setForm] = useState<PoFormValues>(() => toPoForm(extraction))
  const [lines, setLines] = useState<LineForm[]>(() => poLinesOf(extraction))
  const [customerId, setCustomerId] = useState('')
  const [asking, setAsking] = useState<'approve' | 'reject' | null>(null)

  const edits = diffPoEdits(extraction, form, lines)
  const changed = Object.keys(edits).length
  const decided = review.status !== 'Pending'
  const busy = approve.isPending || reject.isPending
  const name = review.file_name ?? 'The document'

  return (
    <>
      <EvidenceLayout
        left={review.source_document_id ? <DocumentViewer documentId={review.source_document_id} fileName={review.file_name} /> : null}
        right={
          <div className="space-y-5">
            <div>
              <h3 className="font-semibold">{review.file_name ?? 'Uploaded purchase order'}</h3>
              <p className="text-sm text-muted-foreground">Check these against the document before the purchase order is saved.</p>
            </div>

            <ReasonsBox title="Why it needs your check" reasons={review.reasons} />

            {decided ? (
              <DecidedNotice status={review.status} reason={review.decision_reason} />
            ) : (
              <>
                <CustomerPicker matchedName={review.matched_customer_name} value={customerId} onChange={setCustomerId} />

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {PO_FIELDS.map((field) => {
                    const sure = extraction.confidence[field.confidence]
                    const low = sure !== null && sure < LOW_CONFIDENCE
                    return (
                      <div key={field.key} className="space-y-1">
                        <Label htmlFor={`f-${field.key}`}>
                          {field.label}
                          {low && <span className="ml-2 text-xs font-normal text-amber-700">{sure}% sure</span>}
                        </Label>
                        <Input
                          id={`f-${field.key}`}
                          type={field.kind === 'date' ? 'date' : 'text'}
                          inputMode={field.kind === 'amount' ? 'decimal' : undefined}
                          value={form[field.key]}
                          onChange={(e) => setForm((current) => ({ ...current, [field.key]: e.target.value }))}
                          aria-invalid={low || undefined}
                        />
                      </div>
                    )
                  })}
                </div>

                <section aria-label="Line items" className="space-y-1">
                  <h4 className="text-sm font-medium">
                    Line items
                    {extraction.confidence.line_items !== null && extraction.confidence.line_items < LOW_CONFIDENCE && (
                      <span className="ml-2 text-xs font-normal text-amber-700">{extraction.confidence.line_items}% sure</span>
                    )}
                  </h4>
                  <LineEditor lines={lines} onChange={setLines} />
                </section>

                <div className="flex flex-col gap-2">
                  {changed > 0 && <p className="text-xs text-muted-foreground">{changed === 1 ? '1 field corrected' : `${changed} fields corrected`}</p>}
                  <Button disabled={busy} onClick={() => setAsking('approve')}>
                    Approve purchase order
                  </Button>
                  <Button variant="destructive" disabled={busy} onClick={() => setAsking('reject')}>
                    Reject this document
                  </Button>
                </div>
              </>
            )}
          </div>
        }
      />

      <ReasonDialog
        open={asking === 'approve'}
        onOpenChange={(open) => !open && setAsking(null)}
        title="Approve this purchase order"
        description="Approving says this document is a purchase order. Invoices waiting for it are linked and checked. Say why you are approving it."
        confirmLabel="Confirm approval"
        pending={approve.isPending}
        onConfirm={(reason) =>
          approve.mutate(
            { reason, customerId: customerId || undefined, edits },
            {
              onSuccess: (decision) => {
                setAsking(null)
                onDone(describePoApproval(decision, form.po_number || 'the purchase order'))
              },
              onError: () => setAsking(null),
            },
          )
        }
      />
      <ReasonDialog
        open={asking === 'reject'}
        onOpenChange={(open) => !open && setAsking(null)}
        title="Reject this document"
        description="It is moved to the rejected list with your reason; the file is kept."
        confirmLabel="Reject document"
        pending={reject.isPending}
        onConfirm={(reason) =>
          reject.mutate(reason, {
            onSuccess: () => {
              setAsking(null)
              onDone(`${name} was rejected.`)
            },
            onError: () => setAsking(null),
          })
        }
      />
    </>
  )
}
