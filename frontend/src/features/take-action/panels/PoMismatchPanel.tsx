import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'

import { EvidenceLayout } from '@/components/common/EvidenceModal'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { ReasonDialog } from '@/components/common/ReasonDialog'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { PERMISSIONS } from '@/config/constants'
import { hasPermission, useCurrentUser } from '@/features/auth'
import { invoiceQuery, type InvoiceLine } from '@/features/invoices'

import { poMismatchQuery, useOverrideMismatch, useRecheckMismatch } from '../queries/poMismatchQueries'
import type { MismatchReview } from '../services/poMismatchService'
import { toLineForms, toLinePayload, type LineForm } from '../utils/lines'
import { LineEditor } from './parts/LineEditor'
import { DecidedNotice, ReasonsBox } from './parts/ReasonsBox'
import type { PanelProps } from './types'

const quantity = (value: string | null) => (value === null ? '—' : String(Number(value)))

/** "PO mismatch": an invoice is on hold because it does not fit its purchase order. There is no reject: fix the lines or override. */
export function PoMismatchPanel({ itemId, onDone }: PanelProps) {
  const review = useQuery(poMismatchQuery(itemId))

  if (review.isPending) return <EvidenceLayout left={<SkeletonBlock lines={8} />} right={<SkeletonBlock lines={6} />} />
  if (review.isError) return <div className="p-4"><ErrorAlert error={review.error} onRetry={() => review.refetch()} /></div>
  return <MismatchBody review={review.data} itemId={itemId} onDone={onDone} />
}

function Comparison({ review }: { review: MismatchReview }) {
  return (
    <div className="space-y-3 p-4">
      <div>
        <h3 className="font-semibold">Invoice {review.invoice_number ?? ''} against purchase order {review.po_number ?? ''}</h3>
        <p className="text-sm text-muted-foreground">Quantities per line: what was ordered, what earlier invoices used, and what is left after this one.</p>
      </div>
      <table className="w-full text-sm" aria-label="Line comparison">
        <thead>
          <tr className="text-left text-muted-foreground">
            <th className="py-1 font-normal">Line</th>
            <th className="py-1 text-right font-normal">Ordered</th>
            <th className="py-1 text-right font-normal">Already invoiced</th>
            <th className="py-1 text-right font-normal">This invoice</th>
            <th className="py-1 text-right font-normal">Left after</th>
          </tr>
        </thead>
        <tbody>
          {review.lines.map((line) => {
            const over = line.remaining_after !== null && Number(line.remaining_after) < 0
            return (
              <tr key={line.invoice_line_item_id} className="border-t align-top">
                <td className="py-1 pr-2">
                  {line.description}
                  {!line.ok && line.reason && <div className="text-xs text-red-700">{line.reason}</div>}
                </td>
                <td className="py-1 text-right">{quantity(line.ordered)}</td>
                <td className="py-1 text-right">{quantity(line.already_invoiced)}</td>
                <td className="py-1 text-right">{quantity(line.this_invoice)}</td>
                <td className={`py-1 text-right ${over ? 'font-semibold text-red-700' : ''}`}>{quantity(line.remaining_after)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function MismatchBody({ review, itemId, onDone }: { review: MismatchReview; itemId: string; onDone: PanelProps['onDone'] }) {
  const user = useCurrentUser()
  const canOverride = hasPermission(user, PERMISSIONS.paymentsPoOverride)
  const override = useOverrideMismatch(itemId)
  const [overriding, setOverriding] = useState(false)
  const [correcting, setCorrecting] = useState(false)
  const decided = review.status !== 'Pending'
  const number = review.invoice_number ?? 'the invoice'

  return (
    <>
      <EvidenceLayout
        left={<Comparison review={review} />}
        right={
          <div className="space-y-5">
            <ReasonsBox title="Why it is on hold" reasons={review.reasons} />

            {decided ? (
              <DecidedNotice status={review.status} reason={review.decision_reason} />
            ) : (
              <>
                <p className="text-sm text-muted-foreground">
                  There is no reject here. The hold is released by correcting the invoice lines so they fit the purchase order, or by an override from someone allowed to.
                </p>

                {correcting ? (
                  <RecheckForm review={review} itemId={itemId} onDone={onDone} onCancel={() => setCorrecting(false)} />
                ) : (
                  <div className="flex flex-col gap-2">
                    <Button disabled={override.isPending} onClick={() => setCorrecting(true)}>
                      Correct the invoice lines
                    </Button>
                    {canOverride ? (
                      <Button variant="outline" disabled={override.isPending} onClick={() => setOverriding(true)}>
                        Approve as it is (override)
                      </Button>
                    ) : (
                      <p className="text-xs text-muted-foreground">Overriding the hold needs a finance manager or an admin.</p>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        }
      />

      <ReasonDialog
        open={overriding}
        onOpenChange={setOverriding}
        title="Approve the invoice as it is"
        description="The invoice's quantities are written against the purchase order even where a line goes over what was ordered. Say why."
        confirmLabel="Confirm override"
        pending={override.isPending}
        onConfirm={(reason) =>
          override.mutate(reason, {
            onSuccess: () => {
              setOverriding(false)
              onDone(`${number} was approved as it is. The hold is released.`)
            },
            onError: () => setOverriding(false),
          })
        }
      />
    </>
  )
}

/** The invoice's saved lines, editable. Sending them replaces the lines; a pass releases the hold, a fail shows the new comparison. */
function RecheckForm({ review, itemId, onDone, onCancel }: { review: MismatchReview; itemId: string; onDone: PanelProps['onDone']; onCancel: () => void }) {
  const invoice = useQuery(invoiceQuery(review.invoice_id))

  if (invoice.isPending) return <SkeletonBlock lines={5} />
  if (invoice.isError) return <ErrorAlert error={invoice.error} onRetry={() => invoice.refetch()} title="Could not load the invoice lines" />
  return <RecheckEditor lines={invoice.data.line_items} number={review.invoice_number} itemId={itemId} onDone={onDone} onCancel={onCancel} />
}

function RecheckEditor({ lines: saved, number, itemId, onDone, onCancel }: { lines: InvoiceLine[]; number: string | null; itemId: string; onDone: PanelProps['onDone']; onCancel: () => void }) {
  const recheck = useRecheckMismatch(itemId)
  const [lines, setLines] = useState<LineForm[]>(() => toLineForms(saved))
  const [note, setNote] = useState('')
  const payload = toLinePayload(lines)

  return (
    <section aria-label="Correct the lines" className="space-y-3">
      <h4 className="text-sm font-medium">Correct the lines of {number ?? 'the invoice'}</h4>
      <LineEditor lines={lines} onChange={setLines} withTaxRate />
      <div className="space-y-1">
        <Label htmlFor="recheck-note">Note (optional)</Label>
        <Textarea id="recheck-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
      </div>
      <div className="flex gap-2">
        <Button
          disabled={recheck.isPending || payload.length === 0}
          onClick={() =>
            recheck.mutate(
              { lines: payload, note: note.trim() || undefined },
              {
                onSuccess: (decision) => {
                  if (decision.status === 'Approved') onDone(`${number ?? 'The invoice'} now fits its purchase order. The hold is released.`)
                  else toast.warning('The corrected lines still do not fit the purchase order. The comparison on the left shows why.')
                },
              },
            )
          }
        >
          Re-check against the purchase order
        </Button>
        <Button variant="outline" disabled={recheck.isPending} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </section>
  )
}
