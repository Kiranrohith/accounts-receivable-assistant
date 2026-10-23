import { useQuery } from '@tanstack/react-query'
import { Link, useCanGoBack, useNavigate, useRouter } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import type { ReactNode } from 'react'

import { DocumentViewer } from '@/components/common/DocumentViewer'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { formatCurrency, formatDate, formatDateTime } from '@/utils/format'

import { invoiceQuery } from '../queries/invoiceQueries'
import type { InvoiceDetail, InvoicePayment } from '../types/invoice'
import { daysOverdue, statusLabel, typeLabel } from '../utils/view'

const number = (value: string | null) => (value === null ? '—' : String(Number(value)))

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
          <dd className="text-right font-medium tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

function Amounts({ invoice }: { invoice: InvoiceDetail }) {
  const foreign = invoice.invoice_currency !== 'INR' && invoice.amount_foreign !== null
  const rows: [string, ReactNode][] = []
  if (foreign) {
    rows.push(['Invoice total', formatCurrency(invoice.amount_foreign, invoice.invoice_currency)])
    rows.push(['Rate when created', `1 ${invoice.invoice_currency} = ${formatCurrency(invoice.fx_rate, 'INR')}`])
  }
  rows.push([foreign ? 'Total in rupees' : 'Invoice total', formatCurrency(invoice.amount_inr, 'INR')])
  if (invoice.subtotal !== null) rows.push(['Before tax', formatCurrency(invoice.subtotal, invoice.invoice_currency)])
  if (invoice.tax_total !== null) rows.push(['Tax', formatCurrency(invoice.tax_total, invoice.invoice_currency)])
  if (invoice.tds_amount !== null && Number(invoice.tds_amount) > 0) rows.push(['TDS the customer deducts', `− ${formatCurrency(invoice.tds_amount, 'INR')}`])
  rows.push(['We expect to receive', formatCurrency(invoice.collectible_total, 'INR')])
  rows.push(['Paid so far', formatCurrency(invoice.amount_paid, 'INR')])
  rows.push(['Still to collect', <span key="v" className="text-base">{formatCurrency(invoice.collectible_balance, 'INR')}</span>])
  return <Facts rows={rows} />
}

function Lines({ invoice }: { invoice: InvoiceDetail }) {
  if (invoice.line_items.length === 0) return <p className="text-sm text-muted-foreground">No line items were read from this invoice.</p>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" aria-label="Line items">
        <thead>
          <tr className="text-left text-muted-foreground">
            <th className="py-1 font-normal">Description</th>
            <th className="py-1 text-right font-normal">Qty</th>
            <th className="py-1 text-right font-normal">Unit price</th>
            <th className="py-1 text-right font-normal">Tax</th>
            <th className="py-1 text-right font-normal">Total</th>
          </tr>
        </thead>
        <tbody>
          {invoice.line_items.map((line, index) => (
            <tr key={index} className="border-t align-top">
              <td className="py-1 pr-2">{line.description}</td>
              <td className="py-1 text-right tabular-nums">{number(line.quantity)}</td>
              <td className="py-1 text-right tabular-nums">{line.unit_price === null ? '—' : formatCurrency(line.unit_price, invoice.invoice_currency)}</td>
              <td className="py-1 text-right tabular-nums">{line.tax_rate === null ? '—' : `${number(line.tax_rate)}%`}</td>
              <td className="py-1 text-right tabular-nums">{line.line_total === null ? '—' : formatCurrency(line.line_total, invoice.invoice_currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function source(payment: InvoicePayment): string {
  return payment.funded_by === 'credit_note' ? `Credit note ${payment.note_number ?? ''}`.trim() : `Payment ${payment.payment_reference ?? ''}`.trim()
}

function Payments({ payments }: { payments: InvoicePayment[] }) {
  if (payments.length === 0) return <p className="text-sm text-muted-foreground">Nothing has been paid against this invoice yet.</p>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" aria-label="Payments">
        <thead>
          <tr className="text-left text-muted-foreground">
            <th className="py-1 font-normal">From</th>
            <th className="py-1 font-normal">When</th>
            <th className="py-1 text-right font-normal">Amount</th>
            <th className="py-1 font-normal">Status</th>
          </tr>
        </thead>
        <tbody>
          {payments.map((p) => (
            <tr key={p.allocation_id} className={p.status === 'Reversed' ? 'border-t align-top text-muted-foreground' : 'border-t align-top'}>
              <td className="py-1 pr-2">
                {source(p)}
                {p.tolerance && (
                  <span className="block text-xs text-muted-foreground">
                    Settled within the {p.tolerance} allowance{p.tolerance_residual && Number(p.tolerance_residual) !== 0 ? ` (${formatCurrency(p.tolerance_residual, 'INR')} left over)` : ''}
                  </span>
                )}
              </td>
              <td className="py-1 pr-2 whitespace-nowrap">{formatDateTime(p.created_at)}</td>
              <td className="py-1 text-right tabular-nums">{formatCurrency(p.allocated_amount, 'INR')}</td>
              <td className="py-1">{p.status === 'Reversed' ? <StatusChip status="Cancelled" label="Reversed" /> : <StatusChip status="Applied" label="Applied" />}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Notices({ invoice }: { invoice: InvoiceDetail }) {
  return (
    <>
      {invoice.po_hold && (
        <p role="status" className="rounded-md border border-orange-300 bg-orange-50 p-3 text-sm text-orange-950">
          This invoice is on hold: it does not match its purchase order.{' '}
          {invoice.po_mismatch_review_id && (
            <Link to="/take-action" search={{ section: 'po_mismatch', item: `po_mismatch:${invoice.po_mismatch_review_id}`, page: 1, size: 25, sort: 'newest' }} className="font-medium underline">
              Decide what to do with it
            </Link>
          )}
        </p>
      )}
      {invoice.is_disputed && (
        <p role="status" className="rounded-md border border-purple-300 bg-purple-50 p-3 text-sm text-purple-950">
          The customer has disputed this invoice. A payment against it may be held until the dispute is settled.
        </p>
      )}
    </>
  )
}

function Body({ invoice }: { invoice: InvoiceDetail }) {
  const late = daysOverdue(invoice)
  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">{invoice.invoice_number}</h1>
          <StatusChip status={invoice.status} label={statusLabel(invoice.status)} />
          {late > 0 && <StatusChip status="Overdue" label={`${late} ${late === 1 ? 'day' : 'days'} overdue`} />}
          {invoice.is_disputed && <StatusChip status="Disputed" />}
          {invoice.po_hold && <StatusChip status="PO-hold" label="PO hold" />}
          {invoice.type === 'DEBIT_NOTE' && <StatusChip status="Debit note" label={typeLabel(invoice.type)} />}
        </div>
        <p className="text-sm text-muted-foreground">
          <Link to="/customers" search={{ customer: invoice.customer_id, page: 1, size: 25 }} className="font-medium text-foreground underline-offset-2 hover:underline">
            {invoice.customer_name}
          </Link>
          {' · '}issued {formatDate(invoice.invoice_date)} · due {formatDate(invoice.due_date)}
          {invoice.version > 1 && ` · version ${invoice.version}`}
        </p>
      </header>

      <Notices invoice={invoice} />

      <Section title="Amounts">
        <Amounts invoice={invoice} />
      </Section>

      <Section title="Line items">
        <Lines invoice={invoice} />
      </Section>

      <Section title="Purchase order">
        {invoice.linked_po ? (
          <p className="text-sm">
            Matched to{' '}
            <Link to="/purchase-orders/$purchaseOrderId" params={{ purchaseOrderId: invoice.linked_po.purchase_order_id }} className="font-medium underline-offset-2 hover:underline">
              {invoice.linked_po.po_number}
            </Link>
            {invoice.linked_po.match_direction && <span className="text-muted-foreground"> ({invoice.linked_po.match_direction})</span>}
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">{invoice.po_number ? `The invoice quotes ${invoice.po_number}, which is not among our purchase orders.` : 'This invoice is not linked to a purchase order.'}</p>
        )}
      </Section>

      <Section title="Payments">
        <Payments payments={invoice.payments} />
      </Section>
    </div>
  )
}

/** One invoice: the original document beside everything we know about it. */
export function InvoiceDetailPage({ invoiceId }: { invoiceId: string }) {
  const invoice = useQuery(invoiceQuery(invoiceId))
  const router = useRouter()
  const navigate = useNavigate()
  const canGoBack = useCanGoBack()
  // Back to the list as the person left it (its tab, search and page); straight to the list if they arrived by a link.
  const back = () => (canGoBack ? router.history.back() : void navigate({ to: '/invoices' }))

  return (
    <>
      <button type="button" onClick={back} className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" aria-hidden /> All invoices
      </button>
      {invoice.isPending ? (
        <SkeletonBlock lines={10} />
      ) : invoice.isError ? (
        <ErrorAlert error={invoice.error} onRetry={() => invoice.refetch()} title="Could not load this invoice" />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(360px,1fr)]">
          <section aria-label="Original document" className="min-h-96 overflow-hidden rounded-lg border bg-card lg:sticky lg:top-4 lg:h-[calc(100vh-8rem)]">
            <DocumentViewer documentId={invoice.data.source_document_id} fileName={invoice.data.source_file_name} />
          </section>
          <Body invoice={invoice.data} />
        </div>
      )}
    </>
  )
}
