import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { Paperclip } from 'lucide-react'
import { useEffect, useState } from 'react'

import { DataTable, type Column } from '@/components/common/DataTable'
import { PageHeader } from '@/components/common/PageHeader'
import { EmptyState } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { Input } from '@/components/ui/input'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { cn } from '@/lib/utils'
import { formatDateTime } from '@/utils/format'

import { mailLogQuery } from '../queries/mailQueries'
import { MAIL_OUTCOMES, type MailLogItem, type MailOutcome } from '../types/mail'
import { classLabel, OUTCOME_LABELS, outcomeOf } from '../utils/view'
import { MailLogModal } from './MailLogModal'

interface MailLogPageProps {
  outcome: MailOutcome
  q: string | undefined
  sort: string | undefined
  dir: 'asc' | 'desc' | undefined
  page: number
  size: number
  /** The mail whose details are open, from the URL. */
  mail: string | undefined
}

const TAB = 'rounded-md border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-muted'

const COLUMNS: Column<MailLogItem>[] = [
  { key: 'received', header: 'Received', sortKey: 'received_at', cell: (m) => <span className="whitespace-nowrap">{formatDateTime(m.received_at)}</span> },
  { key: 'sender', header: 'From', sortKey: 'sender', cell: (m) => <span className="block max-w-56 truncate">{m.sender ?? 'unknown'}</span> },
  {
    key: 'subject',
    header: 'Subject',
    sortKey: 'subject',
    cell: (m) => (
      <span className="block max-w-md">
        <span className="line-clamp-1 font-medium">{m.subject ?? '(no subject)'}</span>
        {m.attachments > 0 && (
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            <Paperclip className="size-3" aria-hidden /> {m.attachments} {m.attachments === 1 ? 'attachment' : 'attachments'}
          </span>
        )}
      </span>
    ),
  },
  {
    key: 'kind',
    header: 'Read as',
    cell: (m) => (
      <span>
        {classLabel(m.suggested_class)}
        {m.confidence !== null && <span className="block text-xs text-muted-foreground">{m.confidence}% sure</span>}
      </span>
    ),
  },
  { key: 'outcome', header: 'What became of it', cell: (m) => <StatusChip status={OUTCOME_LABELS[outcomeOf(m)]} /> },
]

/** Every mail we have read, with what became of it: a tab per outcome, a search, and a click for the mail and its history. */
export function MailLogPage({ outcome, q, sort, dir, page, size, mail }: MailLogPageProps) {
  const navigate = useNavigate({ from: '/mail' })
  const log = useQuery(mailLogQuery({ outcome, q, sort, dir, page, size }))

  const [text, setText] = useState(q ?? '')
  const debounced = useDebouncedValue(text)
  useEffect(() => {
    if (debounced !== (q ?? '')) void navigate({ search: (prev) => ({ ...prev, q: debounced || undefined, page: 1 }), replace: true })
  }, [debounced]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <PageHeader title="Mail log" description="Every mail we have read, and what became of it." />

      <nav aria-label="Outcomes" className="mb-4 flex flex-wrap gap-2">
        {MAIL_OUTCOMES.map((o) => (
          <Link key={o} from="/mail" to="/mail" search={(prev) => ({ ...prev, outcome: o, page: 1 })} aria-current={outcome === o ? 'page' : undefined} className={cn(TAB, outcome === o && 'border-primary bg-accent text-accent-foreground')}>
            {OUTCOME_LABELS[o]}
          </Link>
        ))}
      </nav>

      <DataTable
        columns={COLUMNS}
        rows={log.data?.items}
        total={log.data?.total ?? 0}
        rowKey={(m) => m.email_id}
        page={page}
        pageSize={size}
        onPageChange={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        onPageSizeChange={(next) => navigate({ search: (prev) => ({ ...prev, size: next, page: 1 }) })}
        sort={sort ? { key: sort, dir: dir ?? 'asc' } : undefined}
        onSortChange={(next) => navigate({ search: (prev) => ({ ...prev, sort: next.key, dir: next.dir, page: 1 }) })}
        isPending={log.isPending}
        error={log.error}
        onRetry={() => log.refetch()}
        onRowClick={(m) => navigate({ search: (prev) => ({ ...prev, mail: m.email_id }) })}
        selectedKey={mail}
        toolbar={<Input type="search" aria-label="Search mail" placeholder="Search by sender or subject" className="max-w-xs" value={text} onChange={(e) => setText(e.target.value)} />}
        empty={<EmptyState message={q || outcome !== 'all' ? 'No mail matches these filters.' : 'No mail has been read yet.'} />}
      />

      <MailLogModal emailId={mail} onClose={() => void navigate({ search: (prev) => ({ ...prev, mail: undefined }) })} />
    </>
  )
}
