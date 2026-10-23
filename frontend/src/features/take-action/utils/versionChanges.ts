import { formatCurrency, formatDate } from '@/utils/format'

import type { VersionChange, VersionDecision } from '../services/versionReviewService'

const MONEY = new Set(['subtotal', 'tax_total', 'total_amount'])
const DATES = new Set(['invoice_date', 'due_date'])

const text = (value: unknown): string => (value === null || value === undefined || value === '' ? '—' : String(value))

/** One value of a change as a person reads it: ₹ amounts, IST dates, "—" for nothing. Lists become one line per entry. */
export function showValue(change: Pick<VersionChange, 'field'>, value: unknown, currency: string): string[] {
  if (change.field === 'tax_breakdown' && Array.isArray(value)) {
    if (value.length === 0) return ['—']
    return value.map(([type, rate, amount]: unknown[]) => `${text(type)}${rate === null || rate === undefined ? '' : ` ${rate}%`} ${formatCurrency(amount as string, currency)}`)
  }
  if (change.field === 'line_items' && Array.isArray(value)) {
    if (value.length === 0) return ['—']
    return value.map((line: Record<string, unknown>) => {
      const quantity = line.quantity === null || line.quantity === undefined ? '' : ` × ${line.quantity}`
      return `${text(line.description)}${quantity} = ${formatCurrency(line.line_total as string | null, currency)}`
    })
  }
  if (value === null || value === undefined || value === '') return ['—']
  if (MONEY.has(change.field)) return [formatCurrency(value as string, currency)]
  if (DATES.has(change.field)) return [formatDate(value as string)]
  return [text(value)]
}

/** The currency to show amounts in: the new document's, which the saved invoice is being changed to. */
export function versionCurrency(proposed: Record<string, unknown>): string {
  const currency = proposed.currency
  return typeof currency === 'string' && currency.trim() !== '' ? currency.trim().toUpperCase() : 'INR'
}

export function describeVersionApproval(decision: VersionDecision, invoiceNumber: string | null): string {
  const name = invoiceNumber ?? 'The invoice'
  const base = decision.version !== null ? `${name} is now version ${decision.version}.` : `${name} was updated.`
  return decision.po_hold ? `${base} It is on hold because the new lines do not match its purchase order.` : base
}
