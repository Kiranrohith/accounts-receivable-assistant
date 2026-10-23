import { keepPreviousData, queryOptions } from '@tanstack/react-query'

import { purchaseOrderService } from '../services/purchaseOrderService'
import type { PurchaseOrderQuery } from '../types/purchaseOrder'

export const purchaseOrderKeys = {
  all: ['purchase-orders'] as const,
  list: (query: PurchaseOrderQuery) => [...purchaseOrderKeys.all, 'list', query] as const,
  detail: (purchaseOrderId: string) => [...purchaseOrderKeys.all, 'detail', purchaseOrderId] as const,
}

/** One page of the purchase order table; the old page stays on screen while the next one loads. */
export const purchaseOrderListQuery = (query: PurchaseOrderQuery) =>
  queryOptions({
    queryKey: purchaseOrderKeys.list(query),
    queryFn: () => purchaseOrderService.search(query),
    placeholderData: keepPreviousData,
  })

export const purchaseOrderQuery = (purchaseOrderId: string) =>
  queryOptions({
    queryKey: purchaseOrderKeys.detail(purchaseOrderId),
    queryFn: () => purchaseOrderService.get(purchaseOrderId),
  })
