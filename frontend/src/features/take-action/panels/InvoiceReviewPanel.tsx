import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { DocumentViewer } from '@/components/common/DocumentViewer'
import { EvidenceLayout } from '@/components/common/EvidenceModal'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { ReasonDialog } from '@/components/common/ReasonDialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { formatCurrency } from '@/utils/format'

import { invoiceReviewQuery, useApproveInvoice, useRejectInvoice } from '../queries/invoiceReviewQueries'
import type { InvoiceReview } from '../services/invoiceReviewService'
import { describeApproval, diffEdits, FIELDS, LOW_CONFIDENCE, toForm, type FormValues } from '../utils/invoiceEdits'
import { CustomerPicker, SELECT_CLASS } from './parts/CustomerPicker'
import { DecidedNotice, ReasonsBox } from './parts/ReasonsBox'
import type { PanelProps } from './types'

/** "Invoice check": the reader took these values from the PDF. Correct what is wrong, then approve or reject. */
export function InvoiceReviewPanel({ itemId, onDone }: PanelProps) {
  const review = useQuery(invoiceReviewQuery(itemId))

  if (review.isPending) return <EvidenceLayout left={<SkeletonBlock lines={8} />} right={<SkeletonBlock lines={10} />} />
  if (review.isError) return <div className="p-4"><ErrorAlert error={review.error} onRetry={() => review.refetch()} /></div>
  return <InvoiceReviewForm review={review.data} itemId={itemId} onDone={onDone} />
}

function InvoiceReviewForm({ review, itemId, onDone }: { review: InvoiceReview; itemId: string; onDone: PanelProps['onDone'] }) {
  const approve = useApproveInvoice(itemId)
  const reject = useRejectInvoice(itemId)

  const extraction = review.extraction
  const [form, setForm] = useState<FormValues>(() => toForm(extraction))
  const [customerId, setCustomerId] = useState('')
  const [fxRate, setFxRate] = useState('')
  const [asking, setAsking] = useState<'approve' | 'reject' | null>(null)

  const edits = diffEdits(extraction, form)
  const changed = Object.keys(edits).length
  const decided = review.status !== 'Pending'
  const busy = approve.isPending || reject.isPending
  const set = (key: keyof FormValues, value: string) => setForm((current) => ({ ...current, [key]: value }))
  const name = review.file_name ?? 'The document'
  const currency = form.currency.trim().toUpperCase()
  const foreign = currency !== '' && currency !== 'INR'

  return (
    <>
      <EvidenceLayout
        left={review.source_document_id ? <DocumentViewer documentId={review.source_document_id} fileName={review.file_name} /> : null}
        right={
          <div className="space-y-5">
            <div>
              <h3 className="font-semibold">{review.file_name ?? 'Uploaded invoice'}</h3>
              <p className="text-sm text-muted-foreground">Check these against the document before the invoice is saved.</p>
            </div>

            <ReasonsBox title="Why it needs your check" reasons={review.reasons} />

            {decided ? (
              <DecidedNotice status={review.status} reason={review.decision_reason} />
            ) : (
              <>
                <CustomerPicker matchedName={review.matched_customer_name} value={customerId} onChange={setCustomerId} />

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div className="space-y-1">
                    <Label htmlFor="f-document_type">Document type</Label>
                    <select id="f-document_type" className={SELECT_CLASS} value={form.document_type} onChange={(e) => set('document_type', e.target.value)}>
                      <option value="INVOICE">Invoice</option>
                      <option value="DEBIT_NOTE">Debit note</option>
                    </select>
                  </div>
                  {FIELDS.map((field) => {
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
                          onChange={(e) => set(field.key, e.target.value)}
                          aria-invalid={low || undefined}
                        />
                      </div>
                    )
                  })}
                </div>

                {foreign && (
                  <div className="space-y-1">
                    <Label htmlFor="f-fx_rate">Rate to INR (only if the ECB has no {currency} rate)</Label>
                    <Input
                      id="f-fx_rate"
                      inputMode="decimal"
                      placeholder={`INR for 1 ${currency}, e.g. 22.75`}
                      value={fxRate}
                      onChange={(e) => setFxRate(e.target.value)}
                    />
                    <p className="text-xs text-muted-foreground">Leave empty to use the ECB rate for the invoice date.</p>
                  </div>
                )}

                {extraction.line_items.length > 0 && (
                  <section aria-label="Line items" className="space-y-1">
                    <h4 className="text-sm font-medium">Line items (as read)</h4>
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-left text-muted-foreground">
                          <th className="py-1 font-normal">Description</th>
                          <th className="py-1 text-right font-normal">Qty</th>
                          <th className="py-1 text-right font-normal">Amount</th>
                        </tr>
                      </thead>
                      <tbody>
                        {extraction.line_items.map((item, index) => (
                          <tr key={index} className="border-t">
                            <td className="py-1 pr-2">{item.description}</td>
                            <td className="py-1 text-right">{item.quantity ?? '—'}</td>
                            <td className="py-1 text-right">{formatCurrency(item.line_total, extraction.currency ?? 'INR')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </section>
                )}

                <div className="flex flex-col gap-2">
                  {changed > 0 && <p className="text-xs text-muted-foreground">{changed === 1 ? '1 field corrected' : `${changed} fields corrected`}</p>}
                  <Button disabled={busy} onClick={() => setAsking('approve')}>
                    Approve invoice
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
        title="Approve this invoice"
        description={changed > 0 ? 'The invoice is saved with your corrections. Say why you are approving it.' : 'The invoice is saved as read. Say why you are approving it.'}
        confirmLabel="Confirm approval"
        pending={approve.isPending}
        onConfirm={(reason) =>
          approve.mutate(
            { reason, customerId: customerId || undefined, fxRate: foreign ? fxRate.trim() || undefined : undefined, edits },
            {
              onSuccess: (decision) => {
                setAsking(null)
                onDone(describeApproval(decision, form.invoice_number || 'the invoice'))
              },
              onError: () => setAsking(null), // the server's message is toasted; the form stays for the fix
            },
          )
        }
      />
      <ReasonDialog
        open={asking === 'reject'}
        onOpenChange={(open) => !open && setAsking(null)}
        title="Reject this document"
        description="It is moved to the rejected invoices with your reason."
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
