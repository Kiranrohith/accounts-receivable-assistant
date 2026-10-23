import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'

import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { DocumentViewer } from '@/components/common/DocumentViewer'
import { EvidenceLayout } from '@/components/common/EvidenceModal'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { PERMISSIONS } from '@/config/constants'
import { hasPermission, useCurrentUser } from '@/features/auth'
import {
  DISPUTE_CATEGORIES,
  disputeQuery,
  useAddNote,
  useDraftEmail,
  useReassignDispute,
  useResolveDispute,
  type DisputeDetail,
  type DraftResult,
} from '@/features/disputes'
import { usersQuery } from '@/features/users'
import { cn } from '@/lib/utils'
import { formatCurrency, formatDate } from '@/utils/format'

import { SELECT_CLASS } from './parts/CustomerPicker'
import { DisputeThread } from './parts/DisputeThread'
import { DraftEditor } from './parts/DraftEditor'
import { DecidedNotice } from './parts/ReasonsBox'
import type { PanelProps } from './types'

const categoryLabel = (value: string | null) => DISPUTE_CATEGORIES.find((c) => c.value === value)?.label ?? value ?? 'Not classified'

/** "My disputes": a complaint assigned to you. Read the thread, write to the customer, note, resolve, or hand it on. */
export function MyDisputePanel({ itemId, onDone }: PanelProps) {
  const dispute = useQuery(disputeQuery(itemId))

  if (dispute.isPending) return <EvidenceLayout left={<SkeletonBlock lines={8} />} right={<SkeletonBlock lines={10} />} />
  if (dispute.isError) return <div className="p-4"><ErrorAlert error={dispute.error} onRetry={() => dispute.refetch()} /></div>
  return <DisputeBody dispute={dispute.data} itemId={itemId} onDone={onDone} />
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

function Facts({ dispute }: { dispute: DisputeDetail }) {
  const invoice = dispute.invoice
  return (
    <div className="space-y-4 text-sm">
      <ul aria-label="Deadlines" className="space-y-0.5">
        <Deadline label="First response" due={dispute.first_response_due} doneAt={dispute.first_response_at} overdue={dispute.first_response_overdue} />
        <Deadline label="Resolution" due={dispute.resolution_due} doneAt={dispute.resolved_at} overdue={dispute.resolution_overdue} />
      </ul>
      {invoice && (
        <section aria-label="The invoice" className="space-y-1">
          <h4 className="font-medium">The invoice</h4>
          <p>
            {invoice.invoice_number} <StatusChip status={invoice.status} /> · due {formatDate(invoice.due_date)} · total {formatCurrency(invoice.collectible_total, invoice.invoice_currency)} · paid{' '}
            {formatCurrency(invoice.amount_paid, invoice.invoice_currency)} · owed {formatCurrency(invoice.collectible_balance, invoice.invoice_currency)}
          </p>
        </section>
      )}
      {dispute.held_payments.length > 0 && (
        <section aria-label="Held payments" className="space-y-1">
          <h4 className="font-medium">Money held for this dispute</h4>
          <ul className="list-disc pl-5">
            {dispute.held_payments.map((p) => (
              <li key={p.payment_id}>
                {p.payment_reference ?? 'Payment'}: {formatCurrency(p.held_amount, 'INR')} ({p.state})
              </li>
            ))}
          </ul>
        </section>
      )}
      {dispute.possibly_related.length > 0 && (
        <section aria-label="Other disputes on this invoice" className="space-y-1">
          <h4 className="font-medium">Other disputes on this invoice</h4>
          <ul className="list-disc pl-5">
            {dispute.possibly_related.map((r) => (
              <li key={r.dispute_id}>
                {categoryLabel(r.category)} · {r.status}
                {r.assigned_to_name && ` · ${r.assigned_to_name}`} · {formatDate(r.received_at)}
                {r.in_same_chain && ' · same conversation'}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

function DisputeBody({ dispute, itemId, onDone }: { dispute: DisputeDetail; itemId: string; onDone: PanelProps['onDone'] }) {
  const canReassign = hasPermission(useCurrentUser(), PERMISSIONS.disputesReassign)
  const draft = useDraftEmail(itemId)
  const note = useAddNote(itemId)
  const resolve = useResolveDispute(itemId)
  const reassign = useReassignDispute(itemId)
  const users = useQuery({ ...usersQuery(), enabled: canReassign })

  const [view, setView] = useState<'thread' | 'document'>('thread')
  const [instructions, setInstructions] = useState('')
  const [noteText, setNoteText] = useState('')
  const [narrative, setNarrative] = useState('')
  const [confirmResolve, setConfirmResolve] = useState(false)
  const [lastDraft, setLastDraft] = useState<DraftResult | null>(null)
  const [closing, setClosing] = useState<DraftResult | null>(null) // the e-mail written when the dispute was resolved
  const [newOwner, setNewOwner] = useState('')
  const [why, setWhy] = useState('')

  const resolved = dispute.status === 'Resolved'
  const pending = [...dispute.messages].reverse().find((m) => m.kind === 'draft')
  const recipient = dispute.from_email
  const sentTo = (to: string) => toast.success(`The e-mail was sent to ${to}.`)
  const owners = (users.data ?? []).filter((u) => u.id !== dispute.assigned_to)

  return (
    <EvidenceLayout
      left={
        <div>
          {dispute.source_document_id && (
            <div className="flex gap-1 border-b p-2" role="group" aria-label="What to show">
              <Button size="sm" variant={view === 'thread' ? 'default' : 'outline'} onClick={() => setView('thread')}>Messages</Button>
              <Button size="sm" variant={view === 'document' ? 'default' : 'outline'} onClick={() => setView('document')}>The complaint document</Button>
            </div>
          )}
          {view === 'document' && dispute.source_document_id ? (
            <DocumentViewer documentId={dispute.source_document_id} fileName="The complaint" />
          ) : (
            <DisputeThread messages={dispute.messages} />
          )}
        </div>
      }
      right={
        <div className="space-y-5">
          <div>
            <h3 className="font-semibold">{dispute.summary ?? 'Customer complaint'}</h3>
            <p className="text-sm text-muted-foreground">
              {dispute.customer_name ?? 'Customer not known'}
              {dispute.invoice_number && ` · invoice ${dispute.invoice_number}`} · {categoryLabel(dispute.category)}
              {dispute.classification_confidence !== null && ` (${dispute.classification_confidence}% sure)`}
              {dispute.payment_related && ' · the customer is withholding payment'}
            </p>
            <p className="mt-1 text-sm">
              <StatusChip status={dispute.status} />
              {dispute.assigned_to_name && <span className="ml-2 text-muted-foreground">Owner: {dispute.assigned_to_name}</span>}
            </p>
          </div>

          <Facts dispute={dispute} />

          {closing ? (
            <section aria-label="Closing e-mail" className="space-y-2">
              <h4 className="font-medium">The dispute is resolved. Tell the customer?</h4>
              <p className="text-sm text-muted-foreground">This e-mail was written from your account of how it was settled. Make it customer-safe, then send it, or finish without one.</p>
              <DraftEditor
                key={closing.message_id}
                disputeId={itemId}
                draft={{ messageId: closing.message_id, subject: closing.subject, body: closing.body, ungrounded: closing.ungrounded, confidence: closing.confidence }}
                recipient={recipient}
                sendLabel="Send and finish"
                onSent={(r) => {
                  sentTo(r.sent_to)
                  onDone('The dispute was resolved and the customer was told.')
                }}
                alternative={<Button variant="outline" onClick={() => onDone('The dispute was resolved without an e-mail to the customer.')}>Finish without an e-mail</Button>}
              />
            </section>
          ) : resolved ? (
            <DecidedNotice status={dispute.status} reason={dispute.resolution_narrative} />
          ) : (
            <>
              <section aria-label="E-mail the customer" className="space-y-2">
                <h4 className="font-medium">E-mail the customer</h4>
                {pending ? (
                  <DraftEditor
                    key={pending.message_id}
                    disputeId={itemId}
                    draft={{
                      messageId: pending.message_id,
                      subject: pending.subject,
                      body: pending.body,
                      ungrounded: lastDraft?.message_id === pending.message_id ? lastDraft.ungrounded : [],
                    }}
                    recipient={recipient}
                    sendLabel="Send to the customer"
                    onSent={(r) => sentTo(r.sent_to)}
                  />
                ) : (
                  <>
                    <Label htmlFor="draft-instructions">What should it cover? (optional)</Label>
                    <Textarea id="draft-instructions" rows={2} maxLength={1000} value={instructions} onChange={(e) => setInstructions(e.target.value)} />
                    <Button variant="outline" disabled={draft.isPending} onClick={() => draft.mutate(instructions.trim() || undefined, { onSuccess: (d) => setLastDraft(d) })}>
                      {draft.isPending ? 'Writing the draft…' : 'Draft an e-mail'}
                    </Button>
                    <p className="text-xs text-muted-foreground">The draft is written from our records of the invoice. Nothing is sent until you confirm.</p>
                  </>
                )}
              </section>

              <section aria-label="Internal note" className="space-y-2">
                <h4 className="font-medium">Internal note</h4>
                <Label htmlFor="dispute-note" className="sr-only">Note</Label>
                <Textarea id="dispute-note" rows={2} maxLength={4000} placeholder="Visible to staff only" value={noteText} onChange={(e) => setNoteText(e.target.value)} />
                <Button variant="outline" disabled={note.isPending || noteText.trim() === ''} onClick={() => note.mutate(noteText.trim(), { onSuccess: () => { setNoteText(''); toast.success('Note added.') } })}>
                  Add note
                </Button>
              </section>

              <section aria-label="Resolve" className="space-y-2">
                <h4 className="font-medium">Resolve</h4>
                <Label htmlFor="dispute-narrative">How was it settled? (required)</Label>
                <Textarea id="dispute-narrative" rows={3} maxLength={4000} value={narrative} onChange={(e) => setNarrative(e.target.value)} />
                <Button disabled={resolve.isPending || narrative.trim().length < 3} onClick={() => setConfirmResolve(true)}>
                  Resolve dispute
                </Button>
              </section>

              {canReassign && (
                <section aria-label="Reassign" className="space-y-2">
                  <h4 className="font-medium">Give it to someone else</h4>
                  <Label htmlFor="dispute-owner">New owner</Label>
                  <select id="dispute-owner" className={SELECT_CLASS} value={newOwner} disabled={users.isPending} onChange={(e) => setNewOwner(e.target.value)}>
                    <option value="">Choose a person…</option>
                    {owners.map((u) => (
                      <option key={u.id} value={u.id}>{u.name} ({u.role})</option>
                    ))}
                  </select>
                  <Label htmlFor="dispute-why">Why (optional)</Label>
                  <Textarea id="dispute-why" rows={2} maxLength={500} value={why} onChange={(e) => setWhy(e.target.value)} />
                  <Button variant="outline" disabled={reassign.isPending || newOwner === ''} onClick={() => reassign.mutate({ assignedTo: newOwner, reason: why.trim() || undefined }, { onSuccess: () => onDone('The dispute was handed to its new owner.') })}>
                    Reassign
                  </Button>
                </section>
              )}
            </>
          )}

          <ConfirmDialog
            open={confirmResolve}
            onOpenChange={setConfirmResolve}
            title="Resolve this dispute"
            description="It is closed and the invoice stops being flagged as disputed. A closing e-mail to the customer is drafted for you to check; it is never sent by itself."
            confirmLabel="Resolve"
            pending={resolve.isPending}
            onConfirm={() =>
              resolve.mutate(narrative.trim(), {
                onSuccess: (result) => {
                  setConfirmResolve(false)
                  if (result.resolution_draft) {
                    setClosing(result.resolution_draft)
                    toast.success('The dispute was resolved.')
                  } else {
                    onDone('The dispute was resolved.')
                  }
                },
                onError: () => setConfirmResolve(false),
              })
            }
          />
        </div>
      }
    />
  )
}
