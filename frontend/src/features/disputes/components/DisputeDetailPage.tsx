import { useQuery } from '@tanstack/react-query'
import { Link, useCanGoBack, useNavigate, useRouter } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { toast } from 'sonner'

import { DocumentViewer } from '@/components/common/DocumentViewer'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { PERMISSIONS } from '@/config/constants'
import { hasPermission, useCurrentUser } from '@/features/auth'
import { usersQuery } from '@/features/users'
import { cn } from '@/lib/utils'
import { formatCurrency, formatDate, formatDateTime } from '@/utils/format'

import { disputeQuery, useReassignDispute } from '../queries/disputeQueries'
import type { DisputeDetail, RelatedDispute } from '../types/dispute'
import { categoryLabel, statusLabel } from '../utils/view'
import { DisputeThread } from './DisputeThread'

const SELECT_CLASS =
  'h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50'

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="space-y-2">
      <h2 className="text-sm font-semibold">{title}</h2>
      {children}
    </section>
  )
}

function Deadline({ label, due, doneAt, overdue }: { label: string; due: string | null; doneAt: string | null; overdue: boolean }) {
  if (doneAt) return <li>{label}: done {formatDate(doneAt)}</li>
  if (!due) return null
  return (
    <li className={cn(overdue && 'font-medium text-red-700')}>
      {label}: due {formatDate(due)}
      {overdue && ' (overdue)'}
    </li>
  )
}

function Related({ dispute, label }: { dispute: RelatedDispute; label: string }) {
  return (
    <li>
      {label}:{' '}
      <Link to="/disputes/$disputeId" params={{ disputeId: dispute.dispute_id }} className="font-medium underline-offset-2 hover:underline">
        {categoryLabel(dispute.category)}, {statusLabel(dispute.status)}
      </Link>{' '}
      <span className="text-muted-foreground">({formatDate(dispute.received_at)})</span>
    </li>
  )
}

function InvoiceFacts({ dispute }: { dispute: DisputeDetail }) {
  const invoice = dispute.invoice
  if (!invoice) return <p className="text-sm text-muted-foreground">This complaint is about the account, not one invoice.</p>
  const currency = 'INR'
  return (
    <div className="space-y-2 text-sm">
      <p>
        <Link to="/invoices/$invoiceId" params={{ invoiceId: invoice.invoice_id }} className="font-medium underline-offset-2 hover:underline">
          {invoice.invoice_number}
        </Link>{' '}
        <StatusChip status={invoice.status} label={invoice.status === 'PartlyPaid' ? 'Part-paid' : invoice.status} /> · due {formatDate(invoice.due_date)}
      </p>
      <p className="text-muted-foreground">
        Expected {formatCurrency(invoice.collectible_total, currency)} · paid {formatCurrency(invoice.amount_paid, currency)} · still to collect {formatCurrency(invoice.collectible_balance, currency)}
      </p>
      {invoice.po_number && (
        <p>
          Purchase order{' '}
          {invoice.purchase_order_id ? (
            <Link to="/purchase-orders/$purchaseOrderId" params={{ purchaseOrderId: invoice.purchase_order_id }} className="font-medium underline-offset-2 hover:underline">
              {invoice.po_number}
            </Link>
          ) : (
            <span className="font-medium">{invoice.po_number}</span>
          )}
          {invoice.po_hold && <span className="text-orange-700"> (invoice on hold)</span>}
        </p>
      )}
      {dispute.payments.length > 0 && (
        <table className="w-full" aria-label="Paid against the invoice">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th className="py-1 font-normal">From</th>
              <th className="py-1 font-normal">When</th>
              <th className="py-1 text-right font-normal">Applied</th>
            </tr>
          </thead>
          <tbody>
            {dispute.payments.map((p, index) => (
              <tr key={index} className={cn('border-t', p.status === 'Reversed' && 'text-muted-foreground')}>
                <td className="py-1 pr-2">
                  {p.funded_by === 'credit_note' ? (
                    `Credit note ${p.note_number ?? ''}`.trim()
                  ) : p.payment_id ? (
                    <Link to="/payments/$paymentId" params={{ paymentId: p.payment_id }} className="underline-offset-2 hover:underline">
                      Payment {p.payment_reference ?? ''}
                    </Link>
                  ) : (
                    `Payment ${p.payment_reference ?? ''}`
                  )}
                  {p.status === 'Reversed' && ' (reversed)'}
                </td>
                <td className="py-1 pr-2 whitespace-nowrap">{formatDateTime(p.created_at)}</td>
                <td className="py-1 text-right tabular-nums">{formatCurrency(p.allocated_amount, 'INR')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function Reassign({ dispute }: { dispute: DisputeDetail }) {
  const users = useQuery(usersQuery())
  const reassign = useReassignDispute(dispute.dispute_id)
  const [owner, setOwner] = useState('')
  const [why, setWhy] = useState('')
  const owners = (users.data ?? []).filter((u) => u.id !== dispute.assigned_to && u.is_active)

  return (
    <Section title="Reassign">
      <div className="space-y-2">
        <Label htmlFor="reassign-owner">New owner</Label>
        <select id="reassign-owner" className={SELECT_CLASS} value={owner} disabled={users.isPending} onChange={(e) => setOwner(e.target.value)}>
          <option value="">Choose a person…</option>
          {owners.map((u) => (
            <option key={u.id} value={u.id}>
              {u.name} ({u.role})
            </option>
          ))}
        </select>
        <Label htmlFor="reassign-why">Why (optional)</Label>
        <Textarea id="reassign-why" rows={2} maxLength={500} value={why} onChange={(e) => setWhy(e.target.value)} />
        <Button
          variant="outline"
          disabled={reassign.isPending || owner === ''}
          onClick={() =>
            reassign.mutate(
              { assignedTo: owner, reason: why.trim() || undefined },
              {
                onSuccess: () => {
                  setOwner('')
                  setWhy('')
                  toast.success('The dispute was handed to its new owner.')
                },
              },
            )
          }
        >
          Reassign
        </Button>
      </div>
    </Section>
  )
}

function Body({ dispute }: { dispute: DisputeDetail }) {
  const user = useCurrentUser()
  const resolved = dispute.status === 'Resolved'
  const mine = !resolved && dispute.status !== 'Raised' && dispute.assigned_to !== null && dispute.assigned_to === user?.id
  const toClassify = dispute.status === 'Raised' && hasPermission(user, PERMISSIONS.disputesClassify)
  const canReassign = !resolved && dispute.status !== 'Raised' && hasPermission(user, PERMISSIONS.disputesReassign)

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">{dispute.summary ?? 'Customer complaint'}</h1>
        <div className="flex flex-wrap items-center gap-2">
          <StatusChip status={dispute.status} label={statusLabel(dispute.status)} />
          <span className="text-sm">{categoryLabel(dispute.category)}</span>
          {dispute.classification_confidence !== null && <span className="text-xs text-muted-foreground">({dispute.classification_confidence}% sure)</span>}
          {dispute.payment_related && <StatusChip status="Held" label="Payment withheld" />}
        </div>
        <p className="text-sm text-muted-foreground">
          {dispute.customer_id ? (
            <Link to="/customers" search={{ customer: dispute.customer_id, page: 1, size: 25 }} className="font-medium text-foreground underline-offset-2 hover:underline">
              {dispute.customer_name}
            </Link>
          ) : (
            'Customer not identified'
          )}
          {' · '}received {formatDate(dispute.received_at)} ({dispute.age_days} {dispute.age_days === 1 ? 'day' : 'days'}
          {resolved ? ' to resolve' : ' ago'})
          {dispute.from_email && ` · from ${dispute.from_email}`}
          {' · '}
          {dispute.assigned_to_name ? `owner ${dispute.assigned_to_name}` : 'no owner yet'}
        </p>
        <div className="flex flex-wrap gap-2">
          {toClassify && (
            <Button asChild size="sm">
              <Link to="/take-action" search={{ section: 'dispute_classify', item: `dispute_classify:${dispute.dispute_id}`, page: 1, size: 25, sort: 'newest' }}>
                Classify it
              </Link>
            </Button>
          )}
          {mine && (
            <Button asChild size="sm">
              <Link to="/take-action" search={{ section: 'my_dispute', item: `my_dispute:${dispute.dispute_id}`, page: 1, size: 25, sort: 'newest' }}>
                Work on it
              </Link>
            </Button>
          )}
        </div>
      </header>

      <Section title="Deadlines">
        <ul className="space-y-0.5 text-sm">
          <Deadline label="First response" due={dispute.first_response_due} doneAt={dispute.first_response_at} overdue={dispute.first_response_overdue} />
          <Deadline label="Resolution" due={dispute.resolution_due} doneAt={dispute.resolved_at} overdue={dispute.resolution_overdue} />
          {dispute.escalated_at && <li>Escalated {formatDate(dispute.escalated_at)}</li>}
        </ul>
      </Section>

      {resolved && (
        <Section title="How it was settled">
          <p className="rounded-md border bg-muted/30 p-3 text-sm">{dispute.resolution_narrative ?? 'No account was recorded.'}</p>
        </Section>
      )}

      <Section title="The invoice">
        <InvoiceFacts dispute={dispute} />
      </Section>

      {dispute.held_payments.length > 0 && (
        <Section title="Money held for this dispute">
          <ul className="space-y-1 text-sm">
            {dispute.held_payments.map((p) => (
              <li key={p.payment_id}>
                <Link to="/payments/$paymentId" params={{ paymentId: p.payment_id }} className="font-medium underline-offset-2 hover:underline">
                  {p.payment_reference ?? 'Payment'}
                </Link>
                : {formatCurrency(p.held_amount, 'INR')} held
              </li>
            ))}
          </ul>
        </Section>
      )}

      {(dispute.earlier_dispute || dispute.later_disputes.length > 0 || dispute.possibly_related.length > 0) && (
        <Section title="Related disputes">
          <ul className="space-y-1 text-sm">
            {dispute.earlier_dispute && <Related dispute={dispute.earlier_dispute} label="Follows" />}
            {dispute.later_disputes.map((d) => (
              <Related key={d.dispute_id} dispute={d} label="Followed by" />
            ))}
            {dispute.possibly_related.map((r) => (
              <li key={r.dispute_id}>
                Also on this invoice:{' '}
                <Link to="/disputes/$disputeId" params={{ disputeId: r.dispute_id }} className="font-medium underline-offset-2 hover:underline">
                  {categoryLabel(r.category)}, {statusLabel(r.status)}
                </Link>
                {r.assigned_to_name && <span className="text-muted-foreground"> · {r.assigned_to_name}</span>}
                <span className="text-muted-foreground"> · {formatDate(r.received_at)}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="History">
        <ol className="space-y-1 text-sm">
          {dispute.timeline.map((entry, index) => (
            <li key={index} className="flex flex-wrap gap-x-3">
              <span className="whitespace-nowrap text-muted-foreground">{formatDateTime(entry.occurred_at)}</span>
              <span>
                {entry.from_state ? `${entry.from_state} → ` : ''}
                <span className="font-medium">{entry.to_state}</span>
                {entry.reason && <span className="text-muted-foreground"> — {entry.reason}</span>}
              </span>
            </li>
          ))}
        </ol>
      </Section>

      {canReassign && <Reassign dispute={dispute} />}
    </div>
  )
}

function Evidence({ dispute }: { dispute: DisputeDetail }) {
  const [view, setView] = useState<'thread' | 'document'>('thread')
  return (
    <section aria-label="Correspondence" className="min-h-96 overflow-hidden rounded-lg border bg-card lg:sticky lg:top-4 lg:h-[calc(100vh-8rem)] lg:overflow-y-auto">
      {dispute.source_document_id && (
        <div className="flex gap-1 border-b p-2" role="group" aria-label="What to show">
          <Button size="sm" variant={view === 'thread' ? 'default' : 'outline'} onClick={() => setView('thread')}>
            Messages
          </Button>
          <Button size="sm" variant={view === 'document' ? 'default' : 'outline'} onClick={() => setView('document')}>
            The complaint document
          </Button>
        </div>
      )}
      {view === 'document' && dispute.source_document_id ? <DocumentViewer documentId={dispute.source_document_id} fileName="The complaint" /> : <DisputeThread messages={dispute.messages} />}
    </section>
  )
}

/** One dispute: what was said beside who has it, how late it is, the invoice and money behind it, and what happened to it. */
export function DisputeDetailPage({ disputeId }: { disputeId: string }) {
  const dispute = useQuery(disputeQuery(disputeId))
  const router = useRouter()
  const navigate = useNavigate()
  const canGoBack = useCanGoBack()
  // Back to the list as the person left it (its tab, search and page); straight to the list if they arrived by a link.
  const back = () => (canGoBack ? router.history.back() : void navigate({ to: '/disputes' }))

  return (
    <>
      <button type="button" onClick={back} className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" aria-hidden /> All disputes
      </button>
      {dispute.isPending ? (
        <SkeletonBlock lines={10} />
      ) : dispute.isError ? (
        <ErrorAlert error={dispute.error} onRetry={() => dispute.refetch()} title="Could not load this dispute" />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(360px,1fr)]">
          <Evidence dispute={dispute.data} />
          <Body dispute={dispute.data} />
        </div>
      )}
    </>
  )
}
