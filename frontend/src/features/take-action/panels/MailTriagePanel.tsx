import { useQuery } from '@tanstack/react-query'
import { ExternalLink } from 'lucide-react'
import { useState } from 'react'

import { EvidenceLayout } from '@/components/common/EvidenceModal'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { triageMailQuery, useClassifyMail, type MailClass, type TriageMail } from '@/features/mail'
import { formatDateTime } from '@/utils/format'

import { DecidedNotice } from './parts/ReasonsBox'
import type { PanelProps } from './types'

/** What a person can say a mail is. The wording is the button text, so it states where the mail goes. */
const ROUTES: { value: MailClass; label: string }[] = [
  { value: 'payment', label: 'It is a payment advice' },
  { value: 'dispute', label: 'It is a customer complaint' },
  { value: 'both', label: 'It is a complaint with a payment advice' },
  { value: 'purchase_order', label: 'It is a purchase order' },
  { value: 'general', label: 'Not for us: set it aside' },
]

const VERDICT: Record<string, string> = { qualify: 'Will be read', skip: 'Skipped', reject: 'Rejected' }

/** "Mail triage": the reader was not sure where this mail belongs. Read it, then route it. */
export function MailTriagePanel({ itemId, onDone }: PanelProps) {
  const mail = useQuery(triageMailQuery(itemId))

  if (mail.isPending) return <EvidenceLayout left={<SkeletonBlock lines={10} />} right={<SkeletonBlock lines={6} />} />
  if (mail.isError) return <div className="p-4"><ErrorAlert error={mail.error} onRetry={() => mail.refetch()} /></div>
  return <MailBody mail={mail.data} itemId={itemId} onDone={onDone} />
}

function MailText({ mail }: { mail: TriageMail }) {
  return (
    <article className="space-y-4 p-4">
      <header className="space-y-1">
        <h3 className="font-semibold">{mail.subject ?? '(no subject)'}</h3>
        <p className="text-sm text-muted-foreground">
          From {mail.sender ?? 'unknown'} · {formatDateTime(mail.received_at)}
        </p>
        {mail.replies_to && <p className="text-sm">Continues our conversation: {mail.replies_to}</p>}
        <a className="inline-flex items-center gap-1 text-sm text-primary hover:underline" href={mail.mail_link} target="_blank" rel="noreferrer">
          Open in Gmail <ExternalLink className="size-3.5" aria-hidden />
        </a>
      </header>
      <pre className="whitespace-pre-wrap rounded-md border bg-muted/30 p-3 font-sans text-sm" aria-label="Mail text">{mail.body || '(the mail has no text)'}</pre>
      <section aria-label="Attachments" className="space-y-1">
        <h4 className="text-sm font-medium">Attachments</h4>
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
    </article>
  )
}

function MailBody({ mail, itemId, onDone }: { mail: TriageMail; itemId: string; onDone: PanelProps['onDone'] }) {
  const classify = useClassifyMail(itemId)
  const [note, setNote] = useState('')
  // No status and no suggestion: the reader never classified it (the models were unreachable), so a person must.
  const unclassified = mail.status === null && mail.suggested_class === null
  const waiting = mail.status === 'HumanTriage' || mail.status === 'Processed' || unclassified

  return (
    <EvidenceLayout
      left={<MailText mail={mail} />}
      right={
        <div className="space-y-5">
          <div>
            <h3 className="font-semibold">Where does this mail belong?</h3>
            <p className="text-sm text-muted-foreground">
              {unclassified ? 'The reader has not classified this mail yet.' : 'The reader was not sure.'} Your choice sends it on to that pipeline.
            </p>
          </div>

          {unclassified && (
            <p role="status" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
              Nobody has classified this mail: the reader could not be reached, or kept failing. Read it and choose where it belongs.
            </p>
          )}

          {mail.suggested_class && (
            <div className="rounded-md border bg-muted/40 p-3 text-sm">
              <p>
                The reader's best guess: <StatusChip status={mail.suggested_class} />
                {mail.confidence !== null && ` (${mail.confidence}% sure)`}
              </p>
              {mail.reason && <p className="mt-1 text-muted-foreground">{mail.reason}</p>}
            </div>
          )}

          {!waiting ? (
            <DecidedNotice status={mail.status ?? 'handled'} reason={null} />
          ) : (
            <>
              <div className="space-y-1">
                <Label htmlFor="route-note">Note (optional)</Label>
                <Textarea id="route-note" rows={2} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
              </div>
              <div className="flex flex-col gap-2">
                {ROUTES.map((route) => (
                  <Button
                    key={route.value}
                    variant={route.value === mail.suggested_class ? 'default' : 'outline'}
                    className="h-auto justify-start whitespace-normal py-2 text-left"
                    disabled={classify.isPending}
                    onClick={() => classify.mutate({ mailClass: route.value, note: note.trim() || undefined }, { onSuccess: (result) => onDone(result.message) })}
                  >
                    {route.label}
                  </Button>
                ))}
              </div>
            </>
          )}
        </div>
      }
    />
  )
}
