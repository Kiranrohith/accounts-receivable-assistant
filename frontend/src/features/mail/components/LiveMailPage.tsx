import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { AlertCircle, Ban, CheckCircle2, Clock, ExternalLink, ListChecks, Loader2, RotateCw, XCircle, type LucideIcon } from 'lucide-react'

import { PageHeader } from '@/components/common/PageHeader'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { cn } from '@/lib/utils'
import { formatDateTime } from '@/utils/format'

import { liveMailQuery } from '../queries/mailQueries'
import type { FinishedHow, LiveDocument, LiveMail, LiveState } from '../types/live'
import { documentKindLabel, howLabel, isFading, placeInLine, reviewTarget } from '../utils/live'
import { classLabel } from '../utils/view'

const STATE_ICON: Record<LiveState, { icon: LucideIcon; className: string }> = {
  running: { icon: Loader2, className: 'animate-spin text-blue-600' },
  waiting: { icon: Clock, className: 'text-muted-foreground' },
  retrying: { icon: RotateCw, className: 'text-amber-600' },
  finished: { icon: CheckCircle2, className: 'text-green-600' },
}

const ENDED_ICON: Record<FinishedHow, { icon: LucideIcon; className: string }> = {
  done: { icon: CheckCircle2, className: 'text-green-600' },
  ignored: { icon: Ban, className: 'text-muted-foreground' },
  review: { icon: ListChecks, className: 'text-orange-600' },
  rejected: { icon: AlertCircle, className: 'text-orange-600' },
  failed: { icon: XCircle, className: 'text-red-600' },
}

function iconFor(state: LiveState, how: FinishedHow | null) {
  return state === 'finished' && how ? ENDED_ICON[how] : STATE_ICON[state]
}

function DocumentLine({ doc }: { doc: LiveDocument }) {
  const { icon: Icon, className } = iconFor(doc.state, doc.finished_how)
  return (
    <li className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
      <Icon className={cn('size-4 shrink-0', className)} aria-hidden />
      <span className="font-medium">{doc.upload.file_name ?? 'Mail text'}</span>
      <span className="text-muted-foreground">{documentKindLabel(doc.upload.document_kind)}</span>
      <span aria-label="Step">{doc.state === 'waiting' ? (placeInLine(doc.ahead) ? `${doc.stage} (${placeInLine(doc.ahead)?.toLowerCase()})` : doc.stage) : doc.stage}</span>
      {doc.finished_how === 'rejected' && doc.upload.disposition_reasons[0] && <span className="text-orange-700">{doc.upload.disposition_reasons[0]}</span>}
      {doc.finished_how === 'failed' && doc.upload.error && <span className="text-red-700">{doc.upload.error}</span>}
    </li>
  )
}

function MailCard({ mail, asOf }: { mail: LiveMail; asOf: string }) {
  const { icon: Icon, className } = iconFor(mail.state, mail.finished_how)
  const target = mail.finished_how === 'review' ? reviewTarget(mail) : null
  const fading = isFading(mail, asOf)
  const line = mail.state === 'waiting' ? placeInLine(mail.ahead) : null
  return (
    <li
      aria-label={mail.subject ?? 'Mail with no subject'}
      className={cn('rounded-lg border bg-card p-4 transition-opacity duration-1000', fading && 'opacity-40', mail.state === 'finished' && mail.finished_how === 'review' && 'border-orange-300')}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="line-clamp-1 font-medium">{mail.subject ?? '(no subject)'}</p>
          <p className="text-xs text-muted-foreground">
            {mail.sender ?? 'unknown sender'}
            {mail.received_at && ` · received ${formatDateTime(mail.received_at)}`}
          </p>
        </div>
        <span className="flex items-center gap-2 text-xs text-muted-foreground">
          {mail.mail_class ? (
            <span className="rounded-full bg-muted px-2 py-0.5 font-medium text-foreground">
              {classLabel(mail.mail_class)}
              {mail.confidence !== null && ` · ${mail.confidence}%`}
            </span>
          ) : (
            <span className="rounded-full bg-muted px-2 py-0.5">Not read yet</span>
          )}
          {mail.mail_link && (
            <a href={mail.mail_link} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:text-foreground">
              Open mail <ExternalLink className="size-3" aria-hidden />
            </a>
          )}
        </span>
      </div>

      <p className="mt-3 flex flex-wrap items-center gap-2 text-sm font-medium" role="status">
        <Icon className={cn('size-4 shrink-0', className)} aria-hidden />
        {mail.finished_how ? howLabel(mail.finished_how) : mail.stage}
        {line && <span className="font-normal text-muted-foreground">· {line}</span>}
        {target && (
          <Link to="/take-action" search={{ section: target.section, item: target.item, sort: 'newest', page: 1, size: 25 }} className="font-normal text-primary hover:underline">
            Decide it in Take Action
          </Link>
        )}
        {mail.finished_how === 'failed' && (
          <Link to="/failed-items" search={{ kind: 'all', page: 1, size: 25 }} className="font-normal text-primary hover:underline">
            See it in Failed Items
          </Link>
        )}
      </p>

      {mail.documents.length > 0 && (
        <ul className="mt-2 space-y-1 border-l-2 pl-3" aria-label="Attachments">
          {mail.documents.map((doc) => (
            <DocumentLine key={doc.upload.source_document_id} doc={doc} />
          ))}
        </ul>
      )}
    </li>
  )
}

/** Mail being read right now: where each one is, step by step, and for a few seconds after, how it ended. A mail leaves the list when it is done. */
export function LiveMailPage() {
  const feed = useQuery(liveMailQuery())
  const busy = feed.data?.items.filter((m) => m.state !== 'finished').length ?? 0
  return (
    <>
      <PageHeader title="Live mail" description="Mail that is being read right now. A mail leaves this list a few seconds after it is done; one that needs a person says where to decide it.">
        <span className="flex items-center gap-2 text-sm text-muted-foreground" aria-live="polite">
          <span className={cn('size-2 rounded-full', feed.isError ? 'bg-red-500' : 'animate-pulse bg-green-500')} aria-hidden />
          {feed.isError ? 'Not updating' : busy > 0 ? `${busy} in progress` : 'Up to date'}
        </span>
      </PageHeader>

      {feed.isPending ? (
        <SkeletonBlock lines={6} />
      ) : feed.isError && !feed.data ? (
        <ErrorAlert error={feed.error} onRetry={() => feed.refetch()} />
      ) : feed.data.items.length === 0 ? (
        <div className="rounded-lg border bg-card p-8 text-center text-sm text-muted-foreground">
          No mail is being processed right now. A mail shows up here the moment it is picked up from the inbox, and leaves a few seconds after it is done.
        </div>
      ) : (
        <ul className="space-y-3">
          {feed.data.items.map((mail) => (
            <MailCard key={mail.email_id} mail={mail} asOf={feed.data.as_of} />
          ))}
        </ul>
      )}
    </>
  )
}
