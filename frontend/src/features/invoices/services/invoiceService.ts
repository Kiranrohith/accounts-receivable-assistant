import { api } from '@/lib/axios'
import type { Page } from '@/types/api'

import type { InvoiceDetail, InvoiceListItem, InvoiceQuery } from '../types/invoice'
import { toSearchParams } from '../utils/view'

export const invoiceService = {
  async get(invoiceId: string): Promise<InvoiceDetail> {
    const { data } = await api.get<InvoiceDetail>(`/invoices/${invoiceId}`)
    return data
  },

  /** One page of the invoice table: filtered, searched and sorted by the server, with the total that matches. */
  async search(query: InvoiceQuery): Promise<Page<InvoiceListItem>> {
    const { data } = await api.get<Page<InvoiceListItem>>('/invoices/search', { params: toSearchParams(query) })
    return data
  },

  /** Every invoice of one customer, newest first. */
  async forCustomer(customerId: string): Promise<InvoiceListItem[]> {
    const { data } = await api.get<InvoiceListItem[]>('/invoices', { params: { customer_id: customerId, limit: 200 } })
    return data
  },
}
