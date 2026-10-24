import { useQuery } from '@tanstack/react-query'
import { Link, useCanGoBack, useNavigate, useRouter } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import type { ReactNode } from 'react'

import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { TimelineLink } from '@/features/audit-log'
import { formatCurrency, formatDateTime } from '@/utils/format'

import { noteQuery } from '../queries/noteQueries'
import type { NoteDetail } from '../types/note'
import { emailLine, statusLabel } from '../utils/view'

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

function Body({ note }: { note: NoteDetail }) {
  const spent = note.spends.filter((s) => s.status === 'Active').reduce((sum, s) => sum + Number(s.amount), 0)
  return (
    <div className="max-w-3xl space-y-6">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">{note.note_number}</h1>
          <StatusChip status={note.status} label={statusLabel(note.status)} />
          <span className="ml-auto">
            <TimelineLink entityType="note" entityId={note.note_id} />
          </span>
        </div>
        <p className="text-sm text-muted-foreground">
          <Link to="/customers" search={{ customer: note.customer_id, page: 1, size: 25 }} className="font-medium text-foreground underline-offset-2 hover:underline">
            {note.customer_name}
          </Link>
          {' · '}created {formatDateTime(note.created_at)}
        </p>
      </header>

      {note.reason && <p className="rounded-md border bg-muted/40 p-3 text-sm">{note.reason}</p>}

      <Section title="Money">
        <Facts
          rows={[
            ['Amount', formatCurrency(note.amount, 'INR')],
            ['Used so far', formatCurrency(spent, 'INR')],
            ['Left to use', formatCurrency(note.remaining_amount, 'INR')],
          ]}
        />
        {!note.money_identity_ok && (
          <p role="alert" className="rounded-md border border-red-300 bg-red-50 p-2 text-sm text-red-900">
            What is left does not equal the amount minus what was used. Tell the person who maintains the system.
          </p>
        )}
      </Section>

      <Section title="Where it came from">
        <Facts
          rows={[
            [
              'Payment',
              note.linked_payment_id ? (
                <Link to="/payments/$paymentId" params={{ paymentId: note.linked_payment_id }} className="underline-offset-2 hover:underline">
                  {note.linked_payment_reference ?? 'Open the payment'}
                </Link>
              ) : (
                '—'
              ),
            ],
            [
              'Invoice it is about',
              note.linked_invoice_id ? (
                <Link to="/invoices/$invoiceId" params={{ invoiceId: note.linked_invoice_id }} className="underline-offset-2 hover:underline">
                  {note.linked_invoice_number ?? 'Open the invoice'}
                </Link>
              ) : (
                '—'
              ),
            ],
          ]}
        />
      </Section>

      <Section title="What it has paid">
        {note.spends.length === 0 ? (
          <p className="text-sm text-muted-foreground">It has not been used on any invoice yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th className="py-1 font-normal">Invoice</th>
                <th className="py-1 font-normal">When</th>
                <th className="py-1 text-right font-normal">Amount</th>
                <th className="py-1 text-right font-normal">Status</th>
              </tr>
            </thead>
            <tbody>
              {note.spends.map((s) => (
                <tr key={s.allocation_id} className="border-t">
                  <td className="py-1">
                    <Link to="/invoices/$invoiceId" params={{ invoiceId: s.invoice_id }} className="font-medium underline-offset-2 hover:underline">
                      {s.invoice_number}
                    </Link>
                  </td>
                  <td className="py-1 text-muted-foreground">{formatDateTime(s.created_at)}</td>
                  <td className="py-1 text-right tabular-nums">{formatCurrency(s.amount, 'INR')}</td>
                  <td className="py-1 text-right">{s.status === 'Reversed' ? <span className="text-muted-foreground">Reversed</span> : 'Active'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section title="E-mail to the customer">
        <p className={note.email.state === 'failed' ? 'text-sm text-red-800' : 'text-sm text-muted-foreground'}>{emailLine(note.email)}</p>
      </Section>

      <Section title="History">
        <ol className="space-y-1 text-sm">
          {note.timeline.map((entry, index) => (
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
    </div>
  )
}

/** One credit note: where it came from, what it has paid, whether the customer was told, and its history. */
export function CreditNoteDetailPage({ noteId }: { noteId: string }) {
  const note = useQuery(noteQuery(noteId))
  const router = useRouter()
  const navigate = useNavigate()
  const canGoBack = useCanGoBack()
  // Back to the list as the person left it (its tab, search and page); straight to the list if they arrived by a link.
  const back = () => (canGoBack ? router.history.back() : void navigate({ to: '/credit-notes' }))

  return (
    <>
      <button type="button" onClick={back} className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" aria-hidden /> All credit notes
      </button>
      {note.isPending ? (
        <SkeletonBlock lines={10} />
      ) : note.isError ? (
        <ErrorAlert error={note.error} onRetry={() => note.refetch()} title="Could not load this credit note" />
      ) : (
        <Body note={note.data} />
      )}
    </>
  )
}
