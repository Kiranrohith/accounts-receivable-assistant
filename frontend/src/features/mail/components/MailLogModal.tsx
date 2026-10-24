import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { ExternalLink } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { PERMISSIONS } from '@/config/constants'
import { hasPermission, useCurrentUser } from '@/features/auth'
import { formatDateTime } from '@/utils/format'

import { triageMailQuery, useRerouteMail, useRouteMailFromLog } from '../queries/mailQueries'
import type { MailClass, TriageMail } from '../types/mail'
import { classLabel, OUTCOME_LABELS, outcomeOf, pipelineName } from '../utils/view'

const VERDICT: Record<string, string> = { qualify: 'Will be read', skip: 'Skipped', reject: 'Rejected' }

/** Where a mail set aside can still be sent; the wording is the button text. */
const ROUTES: { value: MailClass; label: string }[] = [
  { value: 'payment', label: 'A payment advice' },
  { value: 'dispute', label: 'A customer complaint' },
  { value: 'both', label: 'A complaint with a payment advice' },
  { value: 'purchase_order', label: 'A purchase order' },
]

interface MailLogModalProps {
  /** The mail the URL names; undefined when none is open. */
  emailId: string | undefined
  onClose: () => void
}

function Produced({ mail }: { mail: TriageMail }) {
  if (!mail.produced || mail.produced.length === 0) return null
  return (
    <section aria-label="What it became" className="space-y-1">
      <h3 className="text-sm font-semibold">What it became</h3>
      <ul className="space-y-1 text-sm">
        {mail.produced.map((p) => (
          <li key={`${p.kind}-${p.id}`}>
            {p.kind === 'payment' ? (
              <Link to="/payments/$paymentId" params={{ paymentId: p.id }} className="font-medium underline-offset-2 hover:underline">
                Payment
              </Link>
            ) : (
              <Link to="/disputes/$disputeId" params={{ disputeId: p.id }} className="font-medium underline-offset-2 hover:underline">
                {p.reference ?? 'Dispute'}
              </Link>
            )}{' '}
            <span className="text-muted-foreground">({p.state})</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function Actions({ mail }: { mail: TriageMail }) {
  const route = useRouteMailFromLog(mail.email_id)
  const reroute = useRerouteMail(mail.email_id)
  const [note, setNote] = useState('')
  const [moving, setMoving] = useState(false)
  const user = useCurrentUser()
  const canClassify = hasPermission(user, PERMISSIONS.mailTriageClassify) // the whole triage: the queue, a handed-on mail
  const canRouteIgnored = canClassify || hasPermission(user, PERMISSIONS.mailIgnoredRoute) // a mail set aside, nothing else
  const outcome = outcomeOf(mail)
  const waiting = outcome === 'waiting' || (mail.status === null && mail.suggested_class === null)

  return (
    <section aria-label="What you can do" className="space-y-3">
      <h3 className="text-sm font-semibold">What you can do</h3>
      {waiting && !canClassify && <p className="text-sm text-muted-foreground">This mail is waiting for a person to say where it belongs. That is done by the finance team members in Take Action.</p>}
      {waiting && canClassify && (
        <p className="text-sm">
          This mail is waiting for a person to say where it belongs.{' '}
          <Link to="/take-action" search={{ section: 'mail_triage', item: `mail_triage:${mail.email_id}`, page: 1, size: 25, sort: 'newest' }} className="font-medium underline">
            Decide in Take Action
          </Link>
        </p>
      )}
      {outcome === 'ignored' && canRouteIgnored && (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">It was set aside as general mail. If that was wrong, send it to the right place.</p>
          <Label htmlFor="log-note" className="text-xs text-muted-foreground">
            Note (optional)
          </Label>
          <Textarea id="log-note" rows={2} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="flex flex-col gap-2">
            {ROUTES.map((r) => (
              <Button
                key={r.value}
                variant="outline"
                className="justify-start"
                disabled={route.isPending}
                onClick={() => route.mutate({ mailClass: r.value, note: note.trim() || undefined }, { onSuccess: (result) => toast.success(result.message) })}
              >
                It is {r.label.toLowerCase()}
              </Button>
            ))}
          </div>
        </div>
      )}
      {mail.reroute_to && canClassify && (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">It was handed on as {mail.suggested_class === 'payment' ? 'a payment advice' : 'a complaint'}. If that was wrong, it can go to the other pipeline.</p>
          <Button variant="outline" onClick={() => setMoving(true)}>
            It is really a {pipelineName(mail.reroute_to)}
          </Button>
          <ConfirmDialog
            open={moving}
            onOpenChange={setMoving}
            title={`Send it as a ${pipelineName(mail.reroute_to)}`}
            description="What the first pipeline made from it is taken back: a payment still under review is rejected, a complaint is withdrawn without any e-mail to the customer. Money already recorded, or a complaint already answered, is not undone: you are told and nothing changes."
            confirmLabel="Send it there"
            pending={reroute.isPending}
            onConfirm={() =>
              reroute.mutate(
                { mailClass: mail.reroute_to!, note: undefined },
                {
                  onSuccess: (result) => {
                    setMoving(false)
                    toast.success(result.message)
                  },
                  onError: () => setMoving(false),
                },
              )
            }
          />
        </div>
      )}
      {outcome === 'reading' && !waiting && <p className="text-sm text-muted-foreground">The reader has classified it and it is about to be handed on.</p>}
      {outcome === 'failed' && <p className="text-sm text-muted-foreground">Reading this mail failed, so nothing was made from it.</p>}
      {outcome === 'routed' && !mail.reroute_to && <p className="text-sm text-muted-foreground">It has been handed on. Nothing more to do with it here.</p>}
    </section>
  )
}

function Body({ mail }: { mail: TriageMail }) {
  const outcome = outcomeOf(mail)
  return (
    <div className="space-y-5">
      <header className="space-y-1 text-sm">
        <p className="text-muted-foreground">
          From {mail.sender ?? 'unknown'} · {formatDateTime(mail.received_at)}
        </p>
        <p className="flex flex-wrap items-center gap-2">
          <StatusChip status={OUTCOME_LABELS[outcome]} />
          <span>{classLabel(mail.suggested_class)}</span>
          {mail.confidence !== null && <span className="text-xs text-muted-foreground">({mail.confidence}% sure)</span>}
        </p>
        {mail.reason && <p className="text-muted-foreground">{mail.reason}</p>}
        {mail.replies_to && <p>Continues our conversation: {mail.replies_to}</p>}
        <a className="inline-flex items-center gap-1 text-primary hover:underline" href={mail.mail_link} target="_blank" rel="noreferrer">
          Open in Gmail <ExternalLink className="size-3.5" aria-hidden />
        </a>
      </header>

      <Actions mail={mail} />
      <Produced mail={mail} />

      <section aria-label="Mail text" className="space-y-1">
        <h3 className="text-sm font-semibold">The mail</h3>
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md border bg-muted/30 p-3 font-sans text-sm">{mail.body || '(the mail has no text)'}</pre>
      </section>

      <section aria-label="Attachments" className="space-y-1">
        <h3 className="text-sm font-semibold">Attachments</h3>
        {mail.attachment_list.length === 0 ? (
          <p className="text-sm text-muted-foreground">None.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {mail.attachment_list.map((a) => (
              <li key={a.file_name}>
                <span className="font-medium">{a.file_name}</span> <span className="text-muted-foreground">— {VERDICT[a.verdict] ?? a.verdict}: {a.reason}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {mail.timeline && mail.timeline.length > 0 && (
        <section aria-label="History" className="space-y-1">
          <h3 className="text-sm font-semibold">History</h3>
          <ol className="space-y-1 text-sm">
            {mail.timeline.map((entry, index) => (
              <li key={index} className="flex flex-wrap gap-x-3">
                <span className="whitespace-nowrap text-muted-foreground">{formatDateTime(entry.at)}</span>
                <span>
                  {entry.from ? `${entry.from} → ` : ''}
                  <span className="font-medium">{entry.to}</span>
                  {entry.reason && <span className="text-muted-foreground"> — {entry.reason}</span>}
                </span>
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  )
}

/** One mail in a centred dialog: who sent it, what it said, what became of it, and the one correction that fits where it ended up. */
export function MailLogModal({ emailId, onClose }: MailLogModalProps) {
  const mail = useQuery({ ...triageMailQuery(emailId ?? ''), enabled: emailId !== undefined })

  return (
    <Dialog open={emailId !== undefined} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-[760px]">
        <DialogHeader>
          <DialogTitle>{mail.data?.subject ?? '(no subject)'}</DialogTitle>
          <DialogDescription>A mail we read</DialogDescription>
        </DialogHeader>
        {mail.isPending ? <SkeletonBlock lines={8} /> : mail.isError ? <ErrorAlert error={mail.error} onRetry={() => mail.refetch()} /> : <Body mail={mail.data} />}
      </DialogContent>
    </Dialog>
  )
}
