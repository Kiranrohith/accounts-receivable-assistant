import type { Customer } from '../types/customer'

export const CUSTOMER_SORTS = ['name', 'tax_id', 'currency', 'open', 'outstanding'] as const
export type CustomerSort = (typeof CUSTOMER_SORTS)[number]

export interface CustomerView {
  q?: string
  sort?: string
  dir?: 'asc' | 'desc'
  page: number
  size: number
}

const SORT_VALUE: Record<CustomerSort, (c: Customer) => string | number> = {
  name: (c) => c.customer_name,
  tax_id: (c) => c.tax_id ?? '',
  currency: (c) => c.billing_currency ?? '',
  open: (c) => c.open_invoices,
  outstanding: (c) => Number(c.outstanding),
}

function compare(a: string | number, b: string | number): number {
  return typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b), undefined, { sensitivity: 'base' })
}

/**
 * The customer list is small and arrives whole (it is not paged by the server), so search, sort and paging are applied
 * here, from the same URL parameters every other list uses.
 */
export function viewCustomers(all: Customer[], view: CustomerView): { rows: Customer[]; total: number } {
  const needle = (view.q ?? '').trim().toLowerCase()
  const matching = needle
    ? all.filter((c) => [c.customer_name, c.tax_id, c.email, c.contact].some((field) => (field ?? '').toLowerCase().includes(needle)))
    : all

  const key = (CUSTOMER_SORTS as readonly string[]).includes(view.sort ?? '') ? (view.sort as CustomerSort) : 'name'
  const factor = view.dir === 'desc' ? -1 : 1
  const sorted = [...matching].sort((a, b) => factor * compare(SORT_VALUE[key](a), SORT_VALUE[key](b)) || a.customer_name.localeCompare(b.customer_name))

  const start = (view.page - 1) * view.size
  return { rows: sorted.slice(start, start + view.size), total: sorted.length }
}
