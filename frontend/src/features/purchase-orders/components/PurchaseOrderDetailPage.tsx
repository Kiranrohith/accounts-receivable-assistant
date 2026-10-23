import { useQuery } from '@tanstack/react-query'
import { Link, useCanGoBack, useNavigate, useRouter } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import type { ReactNode } from 'react'

import { DocumentViewer } from '@/components/common/DocumentViewer'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { TimelineLink } from '@/features/audit-log'
import { formatCurrency, formatDate } from '@/utils/format'

import { purchaseOrderQuery } from '../queries/purchaseOrderQueries'
import type { PurchaseOrderDetail } from '../types/purchaseOrder'
import { quantity, remainingNote } from '../utils/view'

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="space-y-2">
      <h2 className="text-sm font-semibold">{title}</h2>
      {children}
    </section>
  )
}

const HOW: Record<string, string> = { po_first: 'Order came first', invoice_first: 'Invoice came first', manual: 'Linked by a person' }

function Lines({ order }: { order: PurchaseOrderDetail }) {
  const currency = order.currency ?? 'INR'
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" aria-label="Lines">
        <thead>
          <tr className="text-left text-muted-foreground">
            <th className="py-1 font-normal">Item</th>
            <th className="py-1 text-right font-normal">Ordered</th>
            <th className="py-1 text-right font-normal">Invoiced</th>
            <th className="py-1 text-right font-normal">Left</th>
            <th className="py-1 text-right font-normal">Unit price</th>
          </tr>
        </thead>
        <tbody>
          {order.lines.map((line) => {
            const left = remainingNote(line.quantity_remaining)
            return (
              <tr key={line.line_id} className="border-t align-top">
                <td className="py-1 pr-2">{line.description}</td>
                <td className="py-1 text-right tabular-nums">{quantity(line.quantity_ordered)}</td>
                <td className="py-1 text-right tabular-nums">{quantity(line.quantity_invoiced)}</td>
                <td className={left.over ? 'py-1 text-right font-medium text-red-700 tabular-nums' : 'py-1 text-right tabular-nums'}>{left.text}</td>
                <td className="py-1 text-right tabular-nums">{line.unit_price === null ? '—' : formatCurrency(line.unit_price, currency)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function Invoices({ order }: { order: PurchaseOrderDetail }) {
  if (order.invoices.length === 0) return <p className="text-sm text-muted-foreground">No invoice is linked to this order yet.</p>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" aria-label="Linked invoices">
        <thead>
          <tr className="text-left text-muted-foreground">
            <th className="py-1 font-normal">Invoice</th>
            <th className="py-1 font-normal">Dated</th>
            <th className="py-1 text-right font-normal">Amount</th>
            <th className="py-1 text-right font-normal">Counted</th>
            <th className="py-1 font-normal">Status</th>
          </tr>
        </thead>
        <tbody>
          {order.invoices.map((i) => (
            <tr key={i.invoice_id} className="border-t align-top">
              <td className="py-1 pr-2">
                <Link to="/invoices/$invoiceId" params={{ invoiceId: i.invoice_id }} className="font-medium underline-offset-2 hover:underline">
                  {i.invoice_number}
                </Link>
                {i.match_direction && <span className="block text-xs text-muted-foreground">{HOW[i.match_direction] ?? i.match_direction}</span>}
              </td>
              <td className="py-1 pr-2 whitespace-nowrap">{formatDate(i.invoice_date)}</td>
              <td className="py-1 pr-2 text-right tabular-nums">{formatCurrency(i.amount_inr, 'INR')}</td>
              <td className="py-1 pr-2 text-right tabular-nums">{quantity(i.quantity_counted)}</td>
              <td className="py-1">
                <span className="flex flex-wrap gap-1">
                  <StatusChip status={i.invoice_status} label={i.invoice_status === 'PartlyPaid' ? 'Part-paid' : i.invoice_status} />
                  {i.po_hold && <StatusChip status="PO-hold" label="On hold" />}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Body({ order }: { order: PurchaseOrderDetail }) {
  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">{order.po_number}</h1>
          <StatusChip status={order.fulfillment} />
          <span className="ml-auto">
            <TimelineLink entityType="purchase_order" entityId={order.purchase_order_id} />
          </span>
        </div>
        <p className="text-sm text-muted-foreground">
          <Link to="/customers" search={{ customer: order.customer_id, page: 1, size: 25 }} className="font-medium text-foreground underline-offset-2 hover:underline">
            {order.customer_name}
          </Link>
          {' · '}dated {formatDate(order.po_date)}
          {order.total_amount !== null && ` · ${formatCurrency(order.total_amount, order.currency ?? 'INR')}`}
        </p>
      </header>

      {order.invoices_on_hold > 0 && (
        <p role="status" className="rounded-md border border-orange-300 bg-orange-50 p-3 text-sm text-orange-950">
          {order.invoices_on_hold === 1 ? 'One invoice linked to this order is' : `${order.invoices_on_hold} invoices linked to this order are`} on hold because they do not match it. Open the invoice to
          decide what to do.
        </p>
      )}

      <Section title="Lines">
        <Lines order={order} />
      </Section>
      <Section title="Linked invoices">
        <Invoices order={order} />
      </Section>
    </div>
  )
}

/** One purchase order: the original document beside its lines, how much of each was invoiced, and the invoices linked to it. */
export function PurchaseOrderDetailPage({ purchaseOrderId }: { purchaseOrderId: string }) {
  const order = useQuery(purchaseOrderQuery(purchaseOrderId))
  const router = useRouter()
  const navigate = useNavigate()
  const canGoBack = useCanGoBack()
  // Back to the list as the person left it (its tab, search and page); straight to the list if they arrived by a link.
  const back = () => (canGoBack ? router.history.back() : void navigate({ to: '/purchase-orders' }))

  return (
    <>
      <button type="button" onClick={back} className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" aria-hidden /> All purchase orders
      </button>
      {order.isPending ? (
        <SkeletonBlock lines={10} />
      ) : order.isError ? (
        <ErrorAlert error={order.error} onRetry={() => order.refetch()} title="Could not load this purchase order" />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(360px,1fr)]">
          <section aria-label="Original document" className="min-h-96 overflow-hidden rounded-lg border bg-card lg:sticky lg:top-4 lg:h-[calc(100vh-8rem)]">
            <DocumentViewer documentId={order.data.source_document_id} fileName={order.data.source_file_name} />
          </section>
          <Body order={order.data} />
        </div>
      )}
    </>
  )
}
