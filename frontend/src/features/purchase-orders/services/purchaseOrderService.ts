import { api } from '@/lib/axios'
import type { Page } from '@/types/api'

import type { PurchaseOrderDetail, PurchaseOrderListItem, PurchaseOrderQuery } from '../types/purchaseOrder'
import { toSearchParams } from '../utils/view'

export const purchaseOrderService = {
  /** One page of the purchase order table: filtered, searched and sorted by the server, with the total that matches. */
  async search(query: PurchaseOrderQuery): Promise<Page<PurchaseOrderListItem>> {
    const { data } = await api.get<Page<PurchaseOrderListItem>>('/purchase-orders/search', { params: toSearchParams(query) })
    return data
  },

  async get(purchaseOrderId: string): Promise<PurchaseOrderDetail> {
    const { data } = await api.get<PurchaseOrderDetail>(`/purchase-orders/${purchaseOrderId}`)
    return data
  },
}
