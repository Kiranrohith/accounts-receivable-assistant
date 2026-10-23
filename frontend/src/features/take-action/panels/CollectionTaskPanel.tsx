import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { EvidenceLayout } from '@/components/common/EvidenceModal'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { taskQuery, useCloseTask, type CloseReason, type TaskDetail } from '@/features/tasks'
import { formatCurrency, formatDate } from '@/utils/format'

import { SELECT_CLASS } from './parts/CustomerPicker'
import { DecidedNotice } from './parts/ReasonsBox'
import type { PanelProps } from './types'

const CLOSE_REASONS: { value: CloseReason; label: string }[] = [
  { value: 'ResolvedManually', label: 'Resolved: I got the money or settled it another way' },
  { value: 'Dismissed', label: 'Dismissed: it should not have been chased' },
]

/** "Collection task": an invoice 60 days overdue was handed to you. Everything needed to chase it is on the left. */
export function CollectionTaskPanel({ itemId, onDone }: PanelProps) {
  const task = useQuery(taskQuery(itemId))

  if (task.isPending) return <EvidenceLayout left={<SkeletonBlock lines={10} />} right={<SkeletonBlock lines={5} />} />
  if (task.isError) return <div className="p-4"><ErrorAlert error={task.error} onRetry={() => task.refetch()} /></div>
  return <TaskBody task={task.data} itemId={itemId} onDone={onDone} />
}

function Bundle({ task }: { task: TaskDetail }) {
  const { invoice, customer, payments = [], reminders = [] } = task.payload ?? {}
  const currency = invoice?.currency ?? 'INR'
  return (
    <div className="space-y-5 p-4">
      <section aria-label="Invoice" className="space-y-1">
        <h3 className="font-semibold">Invoice {invoice?.invoice_number ?? task.invoice_number ?? ''}</h3>
        {invoice ? (
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-muted-foreground">Invoice date</dt><dd>{formatDate(invoice.invoice_date)}</dd>
            <dt className="text-muted-foreground">Due</dt><dd>{formatDate(invoice.due_date)} ({invoice.days_overdue} days overdue)</dd>
            <dt className="text-muted-foreground">Total to collect</dt><dd>{formatCurrency(invoice.collectible_total, currency)}</dd>
            <dt className="text-muted-foreground">Paid so far</dt><dd>{formatCurrency(invoice.amount_paid, currency)}</dd>
            <dt className="text-muted-foreground">Still owed</dt><dd className="font-semibold">{formatCurrency(invoice.balance, currency)}</dd>
          </dl>
        ) : (
          <p className="text-sm text-muted-foreground">No invoice details were kept with this task.</p>
        )}
      </section>

      <section aria-label="Customer contact" className="space-y-1">
        <h4 className="text-sm font-medium">Who to contact</h4>
        {customer ? (
          <p className="text-sm">
            {customer.name}
            {customer.contact && ` · ${customer.contact}`}
            {customer.email && <> · <a className="text-primary hover:underline" href={`mailto:${customer.email}`}>{customer.email}</a></>}
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">{task.customer_name ?? 'Unknown customer'}</p>
        )}
      </section>

      <section aria-label="Payments so far" className="space-y-1">
        <h4 className="text-sm font-medium">Payments applied</h4>
        {payments.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing has been paid on it.</p>
        ) : (
          <ul className="space-y-0.5 text-sm">
            {payments.map((p, i) => (
              <li key={i}>
                {formatDate(p.at)}: {formatCurrency(p.amount, currency)} via {p.payment_reference ?? p.credit_note ?? 'a payment'}
                {p.status !== 'Active' && <span className="text-muted-foreground"> ({p.status})</span>}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Reminders so far" className="space-y-1">
        <h4 className="text-sm font-medium">Reminders already sent</h4>
        {reminders.length === 0 ? (
          <p className="text-sm text-muted-foreground">None.</p>
        ) : (
          <ul className="space-y-0.5 text-sm">
            {reminders.map((r) => (
              <li key={r.reminder_id}>
                Tier {r.tier}: {r.state}
                {r.sent_at && ` on ${formatDate(r.sent_at)}`}
                {r.sent_to && ` to ${r.sent_to}`}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

function TaskBody({ task, itemId, onDone }: { task: TaskDetail; itemId: string; onDone: PanelProps['onDone'] }) {
  const close = useCloseTask(itemId)
  const [reason, setReason] = useState<CloseReason>('ResolvedManually')
  const [note, setNote] = useState('')
  const number = task.invoice_number ?? 'the invoice'

  return (
    <EvidenceLayout
      left={<Bundle task={task} />}
      right={
        <div className="space-y-5">
          <div>
            <h3 className="font-semibold">Chase {number}</h3>
            <p className="text-sm text-muted-foreground">Reminders have stopped; this is now yours to collect. Close the task when it is settled, or if it never should have been chased. It also closes by itself when the invoice is paid.</p>
          </div>

          {task.status === 'Closed' ? (
            <DecidedNotice status={`Closed, ${task.closed_reason ?? ''}`.replace(/, $/, '')} reason={task.closed_note} />
          ) : (
            <>
              <div className="space-y-1">
                <Label htmlFor="close-reason">How did it end?</Label>
                <select id="close-reason" className={SELECT_CLASS} value={reason} onChange={(e) => setReason(e.target.value as CloseReason)}>
                  {CLOSE_REASONS.map((r) => (
                    <option key={r.value} value={r.value}>{r.label}</option>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="close-note">What happened (required)</Label>
                <Textarea id="close-note" rows={4} value={note} onChange={(e) => setNote(e.target.value)} />
              </div>
              <Button
                disabled={close.isPending || note.trim() === ''}
                onClick={() => close.mutate({ reason, note: note.trim() }, { onSuccess: () => onDone(`The task for ${number} was closed.`) })}
              >
                Close the task
              </Button>
            </>
          )}
        </div>
      }
    />
  )
}
