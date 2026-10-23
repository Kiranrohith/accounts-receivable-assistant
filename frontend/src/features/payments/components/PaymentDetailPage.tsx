import { useQuery } from '@tanstack/react-query'
import { Link, useCanGoBack, useNavigate, useRouter } from '@tanstack/react-router'
import { ArrowLeft, Undo2 } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { toast } from 'sonner'

import { DocumentViewer } from '@/components/common/DocumentViewer'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { TimelineLink } from '@/features/audit-log'
import { Button } from '@/components/ui/button'
import { PERMISSIONS } from '@/config/constants'
import { hasPermission, useCurrentUser } from '@/features/auth'
import { formatCurrency, formatDate, formatDateTime } from '@/utils/format'

import { paymentQuery, useReversePayment } from '../queries/paymentQueries'
import type { Allocation, PaymentDetail } from '../types/payment'
import { canReverse, decisionQueue, layerLabel, stateLabel, toleranceLabel } from '../utils/view'
import { ReverseDialog } from './ReverseDialog'

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="space-y-2">
      <h2 className="text-sm font-semibold">{title}</h2>
      {children}
    </section>
  )
}

function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="text-right font-medium">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

function Money({ payment }: { payment: PaymentDetail }) {
  const foreign = payment.currency && payment.currency !== 'INR' && payment.amount
  const rows: [string, ReactNode][] = []
  if (foreign) rows.push(['Received', formatCurrency(payment.amount, payment.currency!)])
  rows.push([foreign ? 'Worth in rupees' : 'Received', payment.amount_received_inr === null ? 'Not known yet' : formatCurrency(payment.amount_received_inr, 'INR')])
  const applied = payment.allocations.filter((a) => a.status === 'Active').reduce((sum, a) => sum + Number(a.allocated_amount), 0)
  rows.push(['Applied to invoices', formatCurrency(applied, 'INR')])
  if (Number(payment.credited_amount) > 0) rows.push(['Turned into credit notes', formatCurrency(payment.credited_amount, 'INR')])
  if (Number(payment.held_amount) > 0) rows.push(['Held for a dispute or a purchase order', formatCurrency(payment.held_amount, 'INR')])
  rows.push(['Not applied', formatCurrency(payment.unapplied_amount, 'INR')])
  return (
    <div className="space-y-2">
      <Facts rows={rows} />
      {!payment.money_identity_ok && (
        <p role="alert" className="rounded-md border border-red-300 bg-red-50 p-2 text-sm text-red-900">
          The amounts on this payment do not add up to what was received. Tell the person who maintains the system.
        </p>
      )}
    </div>
  )
}

function Advice({ payment }: { payment: PaymentDetail }) {
  const rows: [string, ReactNode][] = [
    ['Paid by', payment.payer_name ?? '—'],
    ['Bank', payment.payer_bank ?? '—'],
    ['Paid on', formatDate(payment.payment_date)],
    ['Reference', payment.reference_number ?? '—'],
    ['Purpose', payment.purpose ?? '—'],
    ['From e-mail', payment.from_email ?? '—'],
  ]
  if (payment.extracted_po) rows.push(['Purchase order on the advice', payment.extracted_po])
  rows.push(['Customer identified by', layerLabel(payment.matched_layer)])
  if (payment.confidence_score !== null) rows.push(['Confidence', `${payment.confidence_score}%`])
  return (
    <div className="space-y-2">
      <Facts rows={rows} />
      {payment.match_reason && <p className="text-sm text-muted-foreground">{payment.match_reason}</p>}
    </div>
  )
}

function Quoted({ payment }: { payment: PaymentDetail }) {
  if (payment.quoted_invoices.length === 0) return null
  return (
    <Section title="Invoices the advice names">
      <ul className="space-y-1 text-sm">
        {payment.quoted_invoices.map((q) => (
          <li key={q.invoice_number} className="flex justify-between gap-4">
            <span>
              <span className="font-medium">{q.invoice_number}</span>
              {!q.known && <span className="text-muted-foreground"> — we have no such invoice for this customer</span>}
            </span>
            <span className="tabular-nums">{q.stated_amount === null ? '—' : formatCurrency(q.stated_amount, 'INR')}</span>
          </li>
        ))}
      </ul>
    </Section>
  )
}

function Allocations({ allocations }: { allocations: Allocation[] }) {
  if (allocations.length === 0) return <p className="text-sm text-muted-foreground">Nothing has been applied to an invoice.</p>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" aria-label="Allocations">
        <thead>
          <tr className="text-left text-muted-foreground">
            <th className="py-1 font-normal">Invoice</th>
            <th className="py-1 text-right font-normal">Applied</th>
            <th className="py-1 font-normal">Settled as</th>
            <th className="py-1 font-normal">Status</th>
          </tr>
        </thead>
        <tbody>
          {allocations.map((a) => {
            const label = toleranceLabel(a.tolerance)
            return (
              <tr key={a.allocation_id} className={a.status === 'Reversed' ? 'border-t align-top text-muted-foreground' : 'border-t align-top'}>
                <td className="py-1 pr-2">
                  <Link to="/invoices/$invoiceId" params={{ invoiceId: a.invoice_id }} className="font-medium underline-offset-2 hover:underline">
                    {a.invoice_number}
                  </Link>
                </td>
                <td className="py-1 pr-2 text-right tabular-nums">{formatCurrency(a.allocated_amount, 'INR')}</td>
                <td className="py-1 pr-2">
                  {label ? (
                    <>
                      Within the {label} allowance
                      {a.tolerance_residual && Number(a.tolerance_residual) !== 0 && <span className="block text-xs text-muted-foreground">{formatCurrency(a.tolerance_residual, 'INR')} difference</span>}
                    </>
                  ) : (
                    'In full'
                  )}
                  {a.fx_rate_used && <span className="block text-xs text-muted-foreground">at {formatCurrency(a.fx_rate_used, 'INR')} per unit</span>}
                </td>
                <td className="py-1">{a.status === 'Reversed' ? <StatusChip status="Cancelled" label="Reversed" /> : <StatusChip status="Applied" label="Active" />}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function Body({ payment }: { payment: PaymentDetail }) {
  const user = useCurrentUser()
  const reverse = useReversePayment(payment.payment_id)
  const [reversing, setReversing] = useState(false)
  const queue = decisionQueue(payment)
  const mayReverse = canReverse(payment) && hasPermission(user, PERMISSIONS.paymentsCorrectionRerun)

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">{payment.reference_number ?? 'Payment without a reference'}</h1>
          <StatusChip status={payment.state} label={stateLabel(payment.state)} />
          {payment.duplicate_of && <StatusChip status="Duplicate" />}
          <span className="ml-auto">
            <TimelineLink entityType="payment" entityId={payment.payment_id} />
          </span>
        </div>
        <p className="text-sm text-muted-foreground">
          {payment.customer_id ? (
            <Link to="/customers" search={{ customer: payment.customer_id, page: 1, size: 25 }} className="font-medium text-foreground underline-offset-2 hover:underline">
              {payment.customer_name}
            </Link>
          ) : (
            'Customer not identified'
          )}
          {' · '}received {formatDateTime(payment.created_at)}
        </p>
        {mayReverse && (
          <Button variant="outline" size="sm" onClick={() => setReversing(true)}>
            <Undo2 /> Reverse
          </Button>
        )}
      </header>

      {queue && (
        <div role="status" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
          <p>This payment is waiting for a person to decide where it goes.</p>
          {payment.reasons.length > 0 && (
            <ul className="mt-1 list-disc pl-5">
              {payment.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
          <Link to="/take-action" search={{ section: queue, item: `${queue}:${payment.payment_id}`, page: 1, size: 25, sort: 'newest' }} className="mt-2 inline-block font-medium underline">
            Decide in Take Action
          </Link>
        </div>
      )}
      {payment.state === 'Held' && (
        <p role="status" className="rounded-md border border-violet-300 bg-violet-50 p-3 text-sm text-violet-950">
          The money is being held while a dispute or a purchase-order mismatch is settled. It is matched again once that is resolved.
        </p>
      )}

      <Section title="Money">
        <Money payment={payment} />
      </Section>
      <Section title="The advice">
        <Advice payment={payment} />
      </Section>
      <Quoted payment={payment} />
      <Section title="Applied to">
        <Allocations allocations={payment.allocations} />
      </Section>

      {payment.notes.length > 0 && (
        <Section title="Credit notes from this payment">
          <ul className="space-y-1 text-sm">
            {payment.notes.map((n) => (
              <li key={n.note_id} className="flex justify-between gap-4">
                <span>
                  <span className="font-medium">{n.note_number}</span> <span className="text-muted-foreground">({n.status})</span>
                </span>
                <span className="tabular-nums">
                  {formatCurrency(n.amount, 'INR')}
                  {Number(n.remaining_amount) !== Number(n.amount) && <span className="text-muted-foreground"> · {formatCurrency(n.remaining_amount, 'INR')} left</span>}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="History">
        <ol className="space-y-1 text-sm">
          {payment.timeline.map((entry, index) => (
            <li key={index} className="flex flex-wrap gap-x-3">
              <span className="whitespace-nowrap text-muted-foreground">{formatDateTime(entry.at)}</span>
              <span>
                {entry.from_state ? `${entry.from_state} → ` : ''}
                <span className="font-medium">{entry.to_state}</span>
                {entry.reason && <span className="text-muted-foreground"> — {entry.reason}</span>}
              </span>
            </li>
          ))}
        </ol>
      </Section>

      <ReverseDialog
        open={reversing}
        onOpenChange={setReversing}
        pending={reverse.isPending}
        error={reverse.error}
        onConfirm={(input) =>
          reverse.mutate(input, {
            onSuccess: (result) => {
              setReversing(false)
              toast.success(result.message)
            },
          })
        }
      />
    </div>
  )
}

/** One payment: the original advice beside what was read from it, what it was applied to and what happened to it. */
export function PaymentDetailPage({ paymentId }: { paymentId: string }) {
  const payment = useQuery(paymentQuery(paymentId))
  const router = useRouter()
  const navigate = useNavigate()
  const canGoBack = useCanGoBack()
  // Back to the list as the person left it (its tab, search and page); straight to the list if they arrived by a link.
  const back = () => (canGoBack ? router.history.back() : void navigate({ to: '/payments' }))

  return (
    <>
      <button type="button" onClick={back} className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" aria-hidden /> All payments
      </button>
      {payment.isPending ? (
        <SkeletonBlock lines={10} />
      ) : payment.isError ? (
        <ErrorAlert error={payment.error} onRetry={() => payment.refetch()} title="Could not load this payment" />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(360px,1fr)]">
          <section aria-label="Original advice" className="min-h-96 overflow-hidden rounded-lg border bg-card lg:sticky lg:top-4 lg:h-[calc(100vh-8rem)]">
            <DocumentViewer documentId={payment.data.source_document_id} fileName={payment.data.file_name} />
          </section>
          <Body payment={payment.data} />
        </div>
      )}
    </>
  )
}
