import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { DocumentViewer } from '@/components/common/DocumentViewer'
import { EvidenceLayout } from '@/components/common/EvidenceModal'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { customerInvoicesQuery } from '@/features/invoices'
import { DISPUTE_CATEGORIES, disputeQuery, useClassifyDispute, type DisputeCategory, type DisputeDetail } from '@/features/disputes'
import { formatCurrency, formatDate, formatDateTime } from '@/utils/format'

import { CustomerPicker, SELECT_CLASS } from './parts/CustomerPicker'
import { DecidedNotice } from './parts/ReasonsBox'
import type { PanelProps } from './types'

const categoryLabel = (value: string) => DISPUTE_CATEGORIES.find((c) => c.value === value)?.label ?? value

/** "Dispute classification": a complaint with no category or owner yet. Say what it is about, and it goes to a finance associate. */
export function DisputeClassifyPanel({ itemId, onDone }: PanelProps) {
  const dispute = useQuery(disputeQuery(itemId))

  if (dispute.isPending) return <EvidenceLayout left={<SkeletonBlock lines={8} />} right={<SkeletonBlock lines={8} />} />
  if (dispute.isError) return <div className="p-4"><ErrorAlert error={dispute.error} onRetry={() => dispute.refetch()} /></div>
  return <ClassifyBody dispute={dispute.data} itemId={itemId} onDone={onDone} />
}

function Complaint({ dispute }: { dispute: DisputeDetail }) {
  if (dispute.source_document_id) return <DocumentViewer documentId={dispute.source_document_id} fileName="The complaint" />
  const inbound = dispute.messages.filter((m) => m.kind === 'inbound_email')
  return (
    <div className="space-y-3 p-4">
      <h3 className="font-semibold">The complaint</h3>
      {inbound.length === 0 ? (
        <p className="text-sm text-muted-foreground">{dispute.summary ?? 'No text was kept with this complaint.'}</p>
      ) : (
        inbound.map((m) => (
          <article key={m.message_id} className="rounded-md border bg-muted/30 p-3 text-sm">
            <p className="text-xs text-muted-foreground">{formatDateTime(m.created_at)}</p>
            <pre className="mt-1 whitespace-pre-wrap font-sans">{m.body}</pre>
          </article>
        ))
      )}
    </div>
  )
}

function ClassifyBody({ dispute, itemId, onDone }: { dispute: DisputeDetail; itemId: string; onDone: PanelProps['onDone'] }) {
  const classify = useClassifyDispute(itemId)
  const [category, setCategory] = useState<DisputeCategory | ''>(dispute.category ?? '')
  const [paymentRelated, setPaymentRelated] = useState(dispute.payment_related)
  const [pickedCustomer, setPickedCustomer] = useState('')
  const [pickedInvoice, setPickedInvoice] = useState('')

  const customerId = dispute.customer_id ?? pickedCustomer
  const needsInvoice = dispute.invoice_id === null
  const invoices = useQuery({ ...customerInvoicesQuery(customerId), enabled: needsInvoice && customerId !== '' })
  const waiting = dispute.status === 'Raised'
  const invoice = dispute.invoice

  return (
    <EvidenceLayout
      left={<Complaint dispute={dispute} />}
      right={
        <div className="space-y-5">
          <div>
            <h3 className="font-semibold">{dispute.summary ?? 'Customer complaint'}</h3>
            <p className="text-sm text-muted-foreground">
              {dispute.customer_name ?? 'Customer not known yet'}
              {dispute.invoice_number && ` · invoice ${dispute.invoice_number}`} · received {formatDateTime(dispute.received_at)}
              {dispute.from_email && ` · from ${dispute.from_email}`}
            </p>
          </div>

          {dispute.category && (
            <p className="rounded-md border bg-muted/40 p-3 text-sm">
              The reader's guess: <strong>{categoryLabel(dispute.category)}</strong>
              {dispute.classification_confidence !== null && ` (${dispute.classification_confidence}% sure)`}. Confirm or change it below.
            </p>
          )}

          {invoice && (
            <section aria-label="The invoice" className="space-y-1 text-sm">
              <h4 className="font-medium">The invoice</h4>
              <p>
                {invoice.invoice_number} <StatusChip status={invoice.status} /> · due {formatDate(invoice.due_date)} · total {formatCurrency(invoice.collectible_total, invoice.invoice_currency)} ·
                paid {formatCurrency(invoice.amount_paid, invoice.invoice_currency)} · owed {formatCurrency(invoice.collectible_balance, invoice.invoice_currency)}
              </p>
              {invoice.po_hold && <p className="text-amber-700">On hold: purchase order mismatch.</p>}
            </section>
          )}

          {dispute.possibly_related.length > 0 && (
            <section aria-label="Other disputes on this invoice" className="space-y-1 text-sm">
              <h4 className="font-medium">Other disputes on this invoice</h4>
              <ul className="list-disc space-y-0.5 pl-5">
                {dispute.possibly_related.map((r) => (
                  <li key={r.dispute_id}>
                    {r.category ? categoryLabel(r.category) : 'Not classified'} · {r.status}
                    {r.assigned_to_name && ` · ${r.assigned_to_name}`} · {formatDate(r.received_at)}
                    {r.in_same_chain && ' · same conversation'}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {!waiting ? (
            <DecidedNotice status={dispute.status} reason={dispute.category ? categoryLabel(dispute.category) : null} />
          ) : (
            <>
              {dispute.customer_id === null && (
                <CustomerPicker
                  matchedName={null}
                  value={pickedCustomer}
                  onChange={(id) => {
                    setPickedCustomer(id)
                    setPickedInvoice('')
                  }}
                  noMatchText="The customer could not be identified. Choose who is complaining, if you know."
                  emptyLabel="Leave it without a customer"
                />
              )}
              {needsInvoice && customerId !== '' && (
                <div className="space-y-1">
                  <Label htmlFor="d-invoice">Which invoice is it about?</Label>
                  <select id="d-invoice" className={SELECT_CLASS} value={pickedInvoice} disabled={invoices.isPending} onChange={(e) => setPickedInvoice(e.target.value)}>
                    <option value="">Not about one invoice</option>
                    {(invoices.data ?? []).map((i) => (
                      <option key={i.invoice_id} value={i.invoice_id}>
                        {i.invoice_number} ({i.status}, due {formatDate(i.due_date)})
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <div className="space-y-1">
                <Label htmlFor="d-category">What is it about?</Label>
                <select id="d-category" className={SELECT_CLASS} value={category} onChange={(e) => setCategory(e.target.value as DisputeCategory | '')}>
                  <option value="">Choose a category…</option>
                  {DISPUTE_CATEGORIES.map((c) => (
                    <option key={c.value} value={c.value}>{c.label}</option>
                  ))}
                </select>
              </div>

              <label className="flex items-start gap-2 text-sm">
                <input type="checkbox" className="mt-1" checked={paymentRelated} onChange={(e) => setPaymentRelated(e.target.checked)} />
                <span>
                  The customer is holding back or short-paying on purpose because of this.
                  <span className="block text-xs text-muted-foreground">A payment against this invoice is then held until the dispute is settled.</span>
                </span>
              </label>

              <Button
                disabled={classify.isPending || category === ''}
                onClick={() =>
                  classify.mutate(
                    {
                      category: category as DisputeCategory,
                      paymentRelated,
                      customerId: dispute.customer_id === null ? pickedCustomer || undefined : undefined,
                      invoiceId: needsInvoice ? pickedInvoice || undefined : undefined,
                    },
                    { onSuccess: () => onDone(`The dispute was classified as ${categoryLabel(category)} and handed to a finance associate.`) },
                  )
                }
              >
                Classify and hand over
              </Button>
            </>
          )}
        </div>
      }
    />
  )
}
