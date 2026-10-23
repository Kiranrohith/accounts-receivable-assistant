import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'

import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { EvidenceLayout } from '@/components/common/EvidenceModal'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { ReasonDialog } from '@/components/common/ReasonDialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { reminderQuery, useApproveReminder, useEditReminder, useRejectReminder, type ReminderDetail } from '@/features/reminders'
import { formatDate, formatDateTime } from '@/utils/format'

import { DecidedNotice } from './parts/ReasonsBox'
import type { PanelProps } from './types'

const TIER_LABEL: Record<number, string> = { 1: 'first reminder', 15: '15 days overdue', 30: '30 days overdue' }

/** "Reminder draft": the system wrote a payment reminder. Edit the wording if needed, then send it or turn it down. */
export function ReminderPanel({ itemId, onDone }: PanelProps) {
  const reminder = useQuery(reminderQuery(itemId))

  if (reminder.isPending) return <EvidenceLayout left={<SkeletonBlock lines={8} />} right={<SkeletonBlock lines={8} />} />
  if (reminder.isError) return <div className="p-4"><ErrorAlert error={reminder.error} onRetry={() => reminder.refetch()} /></div>
  return <ReminderBody reminder={reminder.data} itemId={itemId} onDone={onDone} />
}

function Preview({ reminder }: { reminder: ReminderDetail }) {
  return (
    <div className="space-y-4 p-4">
      <div>
        <h3 className="font-semibold">Invoice {reminder.invoice_number} · {reminder.customer_name}</h3>
        <p className="text-sm text-muted-foreground">
          Tier {reminder.tier} ({TIER_LABEL[reminder.tier] ?? `${reminder.tier} days`}) · {reminder.days_overdue ?? '—'} days overdue · balance {reminder.balance ?? '—'}
          {reminder.scheduled_date && ` · due for sending ${formatDate(reminder.scheduled_date)}`}
        </p>
      </div>
      {reminder.state === 'Sent' ? (
        <section aria-label="What the customer received" className="space-y-2">
          <h4 className="text-sm font-medium">What the customer received</h4>
          <div className="rounded-md border bg-muted/30 p-3 text-sm">
            <p className="font-medium">{reminder.sent_subject ?? reminder.subject}</p>
            <pre className="mt-2 whitespace-pre-wrap font-sans">{reminder.sent_body ?? reminder.body}</pre>
          </div>
          <p className="text-xs text-muted-foreground">Sent {formatDateTime(reminder.sent_at)} to {reminder.recipient ?? 'the customer'}{reminder.handled_by ? ` by ${reminder.handled_by}` : ''}.</p>
        </section>
      ) : (
        <section aria-label="What the customer would receive" className="space-y-2">
          <h4 className="text-sm font-medium">What the customer would receive right now</h4>
          <div className="rounded-md border bg-muted/30 p-3 text-sm">
            <p className="font-medium">{reminder.preview_subject}</p>
            <pre className="mt-2 whitespace-pre-wrap font-sans">{reminder.preview_body}</pre>
          </div>
          <p className="text-xs text-muted-foreground">The balance and the days overdue are filled in again at the moment of sending.</p>
        </section>
      )}
      {reminder.history.length > 0 && (
        <section aria-label="History" className="space-y-1">
          <h4 className="text-sm font-medium">History</h4>
          <ul className="space-y-0.5 text-sm text-muted-foreground">
            {reminder.history.map((h, i) => (
              <li key={i}>
                {formatDate(h.at)}: {h.from_state ? `${h.from_state} → ` : ''}{h.to_state}
                {h.reason ? ` (${h.reason})` : ''}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

function ReminderBody({ reminder, itemId, onDone }: { reminder: ReminderDetail; itemId: string; onDone: PanelProps['onDone'] }) {
  const edit = useEditReminder(itemId)
  const approve = useApproveReminder(itemId)
  const reject = useRejectReminder(itemId)
  const [subject, setSubject] = useState(reminder.subject)
  const [body, setBody] = useState(reminder.body)
  const [asking, setAsking] = useState<'send' | 'reject' | null>(null)

  const edits = { ...(subject.trim() !== reminder.subject.trim() && { subject: subject.trim() }), ...(body.trim() !== reminder.body.trim() && { body: body.trim() }) }
  const changed = Object.keys(edits).length > 0
  const decided = reminder.state !== 'Drafted'
  const busy = edit.isPending || approve.isPending || reject.isPending
  const missingBalance = !body.includes('[[balance]]')

  return (
    <>
      <EvidenceLayout
        left={<Preview reminder={reminder} />}
        right={
          <div className="space-y-5">
            <div>
              <h3 className="font-semibold">Reminder {reminder.reference}</h3>
              <p className="text-sm text-muted-foreground">To {reminder.recipient ?? 'no valid address'} (the customer's address cannot be changed).</p>
            </div>

            {decided ? (
              <DecidedNotice status={reminder.state} reason={reminder.note} />
            ) : (
              <>
                {!reminder.can_send && (
                  <p role="alert" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
                    It cannot be sent: {reminder.blocked_reason ?? 'it is no longer needed.'}
                  </p>
                )}
                <div className="space-y-1">
                  <Label htmlFor="rem-subject">Subject</Label>
                  <Input id="rem-subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="rem-body">Message</Label>
                  <Textarea id="rem-body" rows={10} value={body} onChange={(e) => setBody(e.target.value)} />
                  <p className="text-xs text-muted-foreground">
                    Keep [[balance]] and [[days_past_due]] where the figures belong: they are filled in when the mail is sent.
                  </p>
                  {missingBalance && <p className="text-xs text-amber-700">The message no longer contains [[balance]], so the customer will not be told how much is due.</p>}
                </div>

                <div className="flex flex-col gap-2">
                  <Button disabled={busy || !reminder.can_send || subject.trim() === '' || body.trim() === ''} onClick={() => setAsking('send')}>
                    Approve and send
                  </Button>
                  <Button
                    variant="outline"
                    disabled={busy || !changed}
                    onClick={() => edit.mutate(edits, { onSuccess: () => toast.success('Draft saved.') })}
                  >
                    Save changes
                  </Button>
                  <Button variant="destructive" disabled={busy} onClick={() => setAsking('reject')}>
                    Reject this draft
                  </Button>
                </div>
              </>
            )}
          </div>
        }
      />

      <ConfirmDialog
        open={asking === 'send'}
        onOpenChange={(open) => !open && setAsking(null)}
        title="Send this reminder"
        description={`It is e-mailed to ${reminder.recipient ?? 'the customer'} now, with the balance and days overdue as they are today.`}
        confirmLabel="Send to the customer"
        pending={approve.isPending}
        onConfirm={() =>
          approve.mutate(edits, {
            onSuccess: (result) => { setAsking(null); onDone(result.message) },
            onError: () => setAsking(null), // the server's reason is toasted; a failed send stays a draft
          })
        }
      />
      <ReasonDialog
        open={asking === 'reject'}
        onOpenChange={(open) => !open && setAsking(null)}
        title="Reject this draft"
        description="Nothing is sent. A later scan may draft this tier again."
        confirmLabel="Reject draft"
        pending={reject.isPending}
        onConfirm={(reason) =>
          reject.mutate(reason, { onSuccess: (result) => { setAsking(null); onDone(result.message) }, onError: () => setAsking(null) })
        }
      />
    </>
  )
}
