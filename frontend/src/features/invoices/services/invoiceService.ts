import { api } from '@/lib/axios'

import type { InvoiceDetail, InvoiceListItem } from '../types/invoice'

export const invoiceService = {
  async get(invoiceId: string): Promise<InvoiceDetail> {
    const { data } = await api.get<InvoiceDetail>(`/invoices/${invoiceId}`)
    return data
  },

  /** Every invoice of one customer, newest first. */
  async forCustomer(customerId: string): Promise<InvoiceListItem[]> {
    const { data } = await api.get<InvoiceListItem[]>('/invoices', { params: { customer_id: customerId, limit: 200 } })
    return data
  },
}
