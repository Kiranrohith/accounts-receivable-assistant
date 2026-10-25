import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'

import { DocumentViewer } from '@/components/common/DocumentViewer'
import { EvidenceLayout } from '@/components/common/EvidenceModal'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { ReasonDialog } from '@/components/common/ReasonDialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { PERMISSIONS } from '@/config/constants'
import { hasPermission, useCurrentUser } from '@/features/auth'
import { customerInvoicesQuery } from '@/features/invoices'
import {
  paymentQuery,
  useConvertUnapplied,
  useEnterAmount,
  useLeaveUnapplied,
  useMatchPayment,
  useRejectPayment,
  type OpenInvoice,
  type PaymentDetail,
} from '@/features/payments'
import { formatCurrency, formatDate } from '@/utils/format'

import { allocatedMinor, fromMinor, matchLines, suggestAmounts, toMinor, type Amounts } from '../utils/allocation'
import { AllocationTable } from './parts/AllocationTable'
import { CustomerPicker, SELECT_CLASS } from './parts/CustomerPicker'
import { ReasonsBox } from './parts/ReasonsBox'
import type { PanelProps } from './types'

/**
 * "Match review" and "Unapplied cash": a payment a person has to place. The advice is on the left; on the right the person says
 * which invoices it pays (or keeps the money aside, or rejects it).
 */
export function PaymentPanel({ itemId, onDone }: PanelProps) {
  const payment = useQuery(paymentQuery(itemId))

  if (payment.isPending) return <EvidenceLayout left={<SkeletonBlock lines={8} />} right={<SkeletonBlock lines={10} />} />
  if (payment.isError) return <div className="p-4"><ErrorAlert error={payment.error} onRetry={() => payment.refetch()} /></div>
  return <PaymentBody payment={payment.data} itemId={itemId} onDone={onDone} />
}

function Facts({ payment }: { payment: PaymentDetail }) {
  const currency = payment.currency ?? 'INR'
  const rows: [string, string][] = [
    ['Reference', payment.reference_number ?? '—'],
    ['Paid by', payment.payer_name ?? '—'],
    ['Amount', payment.amount === null ? 'Not stated on the advice' : formatCurrency(payment.amount, currency)],
    ['Date', formatDate(payment.payment_date)],
    ['Customer', payment.customer_name ?? 'Not known yet'],
  ]
  if (toMinor(payment.unapplied_amount) > 0) rows.push(['Not yet applied', formatCurrency(payment.unapplied_amount, 'INR')])
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="font-medium">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

function Quoted({ payment }: { payment: PaymentDetail }) {
  if (payment.quoted_invoices.length === 0) return null
  return (
    <section aria-label="Invoices named on the advice" className="space-y-1">
      <h4 className="text-sm font-medium">The advice names</h4>
      <ul className="space-y-0.5 text-sm">
        {payment.quoted_invoices.map((q) => (
          <li key={q.invoice_number}>
            {q.invoice_number}
            {q.stated_amount !== null && ` — ${formatCurrency(q.stated_amount, payment.currency ?? 'INR')}`}
            <span className="text-muted-foreground">
              {!q.known ? ' (we have no such invoice for this customer)' : q.invoice_status === 'Paid' ? ' (already paid)' : ''}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function PaymentBody({ payment, itemId, onDone }: { payment: PaymentDetail; itemId: string; onDone: PanelProps['onDone'] }) {
  const user = useCurrentUser()
  const match = useMatchPayment(itemId)
  const leave = useLeaveUnapplied(itemId)
  const reject = useRejectPayment(itemId)
  const enter = useEnterAmount(itemId)
  const convert = useConvertUnapplied(itemId)

  const [pickedCustomer, setPickedCustomer] = useState('')
  const [amounts, setAmounts] = useState<Amounts>(() => suggestAmounts(payment.quoted_invoices, payment.open_invoices))
  const [note, setNote] = useState('')
  const [alias, setAlias] = useState<'' | 'name' | 'email'>('')
  const [enteredAmount, setEnteredAmount] = useState('')
  const [enteredCurrency, setEnteredCurrency] = useState('')
  const [enteredReference, setEnteredReference] = useState(payment.reference_number ?? '')
  const [asking, setAsking] = useState<'unapplied' | 'reject' | 'convert' | null>(null)

  const hasCustomer = payment.customer_id !== null
  const customerId = payment.customer_id ?? pickedCustomer
  // A payment with no customer yet shows the invoices of the customer the person picks.
  const picked = useQuery({ ...customerInvoicesQuery(pickedCustomer), enabled: !hasCustomer && pickedCustomer !== '' })
  const invoices: OpenInvoice[] = hasCustomer
    ? payment.open_invoices
    : (picked.data ?? []).filter((i) => i.status !== 'Paid').map((i) => ({ ...i, due_date: i.due_date }))

  const currency = payment.currency ?? 'INR'
  const amountKnown = payment.amount !== null
  const paymentMinor = toMinor(payment.amount ?? '')
  const allocated = allocatedMinor(amounts)
  const over = allocated > paymentMinor
  const lines = matchLines(amounts)
  const busy = match.isPending || leave.isPending || reject.isPending || enter.isPending || convert.isPending
  const canAlias = hasPermission(user, PERMISSIONS.customersAliasConfirm) && customerId !== '' && (payment.payer_name !== null || payment.from_email !== null)
  const canConvert = hasPermission(user, PERMISSIONS.notesCreate)

  const needsReview = payment.state === 'NeedsReview'
  const unapplied = payment.state === 'Unapplied'
  const decidable = needsReview || unapplied

  return (
    <>
      <EvidenceLayout
        left={payment.source_document_id ? <DocumentViewer documentId={payment.source_document_id} fileName={payment.file_name} /> : <p className="p-4 text-sm text-muted-foreground">There is no advice file for this payment.</p>}
        right={
          <div className="space-y-5">
            <div>
              <h3 className="font-semibold">{unapplied ? 'Money waiting to be applied' : 'Payment to place'}</h3>
              <p className="text-sm text-muted-foreground">{payment.file_name ?? 'Payment advice'}</p>
            </div>

            <Facts payment={payment} />
            <ReasonsBox title="Why it needs a person" reasons={payment.reasons} />
            <Quoted payment={payment} />

            {!decidable ? (
              <p className="rounded-md border bg-muted/40 p-3 text-sm">This payment is now {payment.state}. There is nothing left to decide.</p>
            ) : !amountKnown ? (
              <section aria-label="Enter the amount" className="space-y-3">
                <p className="text-sm">The advice did not state an amount. Enter it from the bank statement and the payment is matched again straight away.</p>
                <div className="space-y-1">
                  <Label htmlFor="entered-amount">Amount received</Label>
                  <Input id="entered-amount" inputMode="decimal" value={enteredAmount} onChange={(e) => setEnteredAmount(e.target.value)} />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="entered-reference">Transaction reference (UTR)</Label>
                  <Input id="entered-reference" value={enteredReference} onChange={(e) => setEnteredReference(e.target.value)} />
                  <p className="text-xs text-muted-foreground">The reference is what shows the money was sent. Copy it from the bank statement.</p>
                </div>
                {payment.currency === null && (
                  <div className="space-y-1">
                    <Label htmlFor="entered-currency">Currency</Label>
                    <Input id="entered-currency" maxLength={3} placeholder="INR" value={enteredCurrency} onChange={(e) => setEnteredCurrency(e.target.value.toUpperCase())} />
                  </div>
                )}
                <Button
                  disabled={busy || toMinor(enteredAmount) <= 0 || enteredReference.trim() === ''}
                  onClick={() =>
                    enter.mutate(
                      {
                        amount: enteredAmount.trim(),
                        currency: payment.currency === null ? enteredCurrency.trim() || undefined : undefined,
                        reference: enteredReference.trim() === (payment.reference_number ?? '') ? undefined : enteredReference.trim(),
                      },
                      {
                        onSuccess: (result) => {
                          if (result.state === 'NeedsReview') toast.info(result.message)
                          else onDone(result.message)
                        },
                      },
                    )
                  }
                >
                  Save amount and match again
                </Button>
              </section>
            ) : (
              <section aria-label="Apply the payment" className="space-y-4">
                {!hasCustomer && (
                  <CustomerPicker
                    matchedName={null}
                    value={pickedCustomer}
                    onChange={(id) => {
                      setPickedCustomer(id)
                      setAmounts({})
                    }}
                    noMatchText="The payer could not be linked to a customer. Choose who paid."
                    emptyLabel="Choose a customer…"
                  />
                )}

                {customerId === '' ? (
                  <p className="text-sm text-muted-foreground">Choose the customer to see the invoices this payment can pay.</p>
                ) : picked.isPending && !hasCustomer ? (
                  <SkeletonBlock lines={3} />
                ) : (
                  <>
                    <AllocationTable invoices={invoices} amounts={amounts} onChange={setAmounts} paymentMinor={paymentMinor} currency={currency} />
                    <p className={`text-sm ${over ? 'font-medium text-red-700' : 'text-muted-foreground'}`} role="status">
                      {over
                        ? `More than the payment: ${formatCurrency(fromMinor(allocated), currency)} against ${formatCurrency(payment.amount, currency)}.`
                        : `Applying ${formatCurrency(fromMinor(allocated), currency)} of ${formatCurrency(payment.amount, currency)}.${
                            allocated < paymentMinor && allocated > 0
                              ? currency === 'INR' ? ' The rest stays unapplied.' : ' A foreign payment must be applied in full.'
                              : ''
                          }`}
                    </p>

                    {canAlias && (
                      <div className="space-y-1">
                        <Label htmlFor="alias">Remember who paid</Label>
                        <select id="alias" className={SELECT_CLASS} value={alias} onChange={(e) => setAlias(e.target.value as '' | 'name' | 'email')}>
                          <option value="">Do not remember</option>
                          {payment.payer_name && <option value="name">Remember the payer name “{payment.payer_name}” for this customer</option>}
                          {payment.from_email && <option value="email">Remember the sender e-mail {payment.from_email} for this customer</option>}
                        </select>
                      </div>
                    )}

                    <div className="space-y-1">
                      <Label htmlFor="match-note">Note (optional)</Label>
                      <Textarea id="match-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
                    </div>

                    <Button
                      disabled={busy || lines.length === 0 || over}
                      onClick={() =>
                        match.mutate(
                          { lines, customerId: hasCustomer ? undefined : pickedCustomer, note: note.trim() || undefined, saveAlias: alias || undefined },
                          { onSuccess: (result) => onDone(result.message) },
                        )
                      }
                    >
                      Apply payment
                    </Button>
                  </>
                )}

                <div className="flex flex-col gap-2 border-t pt-4">
                  {needsReview && (
                    <>
                      <Button variant="outline" disabled={busy || customerId === ''} onClick={() => setAsking('unapplied')}>
                        Keep as unapplied cash
                      </Button>
                      {customerId === '' && <p className="text-xs text-muted-foreground">Choose the customer first: unapplied cash is held on their account.</p>}
                    </>
                  )}
                  {unapplied && canConvert && (
                    <Button variant="outline" disabled={busy} onClick={() => setAsking('convert')}>
                      Turn the money into a credit note
                    </Button>
                  )}
                </div>
              </section>
            )}

            {needsReview && (
              <Button variant="destructive" disabled={busy} onClick={() => setAsking('reject')}>
                Reject this payment
              </Button>
            )}
          </div>
        }
      />

      <ReasonDialog
        open={asking === 'unapplied'}
        onOpenChange={(open) => !open && setAsking(null)}
        title="Keep as unapplied cash"
        description="The whole amount is held on the customer's account. You can apply it to invoices later."
        confirmLabel="Keep as unapplied"
        pending={leave.isPending}
        onConfirm={(reason) =>
          leave.mutate(
            { reason, customerId: hasCustomer ? undefined : pickedCustomer },
            { onSuccess: (result) => { setAsking(null); onDone(result.message) }, onError: () => setAsking(null) },
          )
        }
      />
      <ReasonDialog
        open={asking === 'reject'}
        onOpenChange={(open) => !open && setAsking(null)}
        title="Reject this payment"
        description="It is not valid or not ours. Every amount on it goes to zero."
        confirmLabel="Reject payment"
        pending={reject.isPending}
        onConfirm={(reason) =>
          reject.mutate(reason, { onSuccess: (result) => { setAsking(null); onDone(result.message) }, onError: () => setAsking(null) })
        }
      />
      <ReasonDialog
        open={asking === 'convert'}
        onOpenChange={(open) => !open && setAsking(null)}
        title="Turn the money into a credit note"
        description="A credit note is created for the customer for all of the unapplied amount."
        confirmLabel="Create credit note"
        pending={convert.isPending}
        onConfirm={(reason) =>
          convert.mutate(reason, {
            onSuccess: (result) => { setAsking(null); onDone(`Credit note ${result.note_number} was created.`) },
            onError: () => setAsking(null),
          })
        }
      />
    </>
  )
}
