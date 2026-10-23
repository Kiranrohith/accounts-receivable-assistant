import { queryOptions } from '@tanstack/react-query'

import { invoiceService } from '../services/invoiceService'

export const invoiceKeys = {
  all: ['invoices'] as const,
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
