import { keepPreviousData, queryOptions } from '@tanstack/react-query'

import { invoiceService } from '../services/invoiceService'
import type { InvoiceQuery } from '../types/invoice'

export const invoiceKeys = {
  all: ['invoices'] as const,
  list: (query: InvoiceQuery) => [...invoiceKeys.all, 'list', query] as const,
  detail: (invoiceId: string) => [...invoiceKeys.all, 'detail', invoiceId] as const,
  forCustomer: (customerId: string) => [...invoiceKeys.all, 'customer', customerId] as const,
}

export const invoiceQuery = (invoiceId: string) =>
  queryOptions({
    queryKey: invoiceKeys.detail(invoiceId),
    queryFn: () => invoiceService.get(invoiceId),
  })

export const customerInvoicesQuery = (customerId: string) =>
  queryOptions({
    queryKey: invoiceKeys.forCustomer(customerId),
    queryFn: () => invoiceService.forCustomer(customerId),
  })

/** One page of the invoice table; the old page stays on screen while the next one loads. */
export const invoiceListQuery = (query: InvoiceQuery) =>
  queryOptions({
    queryKey: invoiceKeys.list(query),
    queryFn: () => invoiceService.search(query),
    placeholderData: keepPreviousData,
  })
