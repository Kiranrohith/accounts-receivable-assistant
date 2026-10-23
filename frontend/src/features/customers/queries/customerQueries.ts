import { queryOptions, useMutation, useQueryClient } from '@tanstack/react-query'

import { customerService } from '../services/customerService'
import type { CustomerEdits } from '../types/customer'

export const customerKeys = {
  all: ['customers'] as const,
  list: () => [...customerKeys.all, 'list'] as const,
  detail: (customerId: string) => [...customerKeys.all, 'detail', customerId] as const,
}

export const customersQuery = () =>
  queryOptions({
    queryKey: customerKeys.list(),
    queryFn: customerService.list,
    staleTime: 60_000, // the list changes rarely, and a decision panel should not refetch it each time it opens
  })

export const customerQuery = (customerId: string) =>
  queryOptions({
    queryKey: customerKeys.detail(customerId),
    queryFn: () => customerService.get(customerId),
  })

/** Every change to a customer refreshes this page's table, its open dialog and every customer picker. */
function useRefreshCustomers() {
  const client = useQueryClient()
  return () => client.invalidateQueries({ queryKey: customerKeys.all })
}

export function useCreateCustomer() {
  const refresh = useRefreshCustomers()
  return useMutation({ mutationFn: customerService.create, onSuccess: refresh })
}

export function useUpdateCustomer(customerId: string) {
  const refresh = useRefreshCustomers()
  return useMutation({ mutationFn: (edits: CustomerEdits) => customerService.update(customerId, edits), onSuccess: refresh })
}

export function useAddAlias(customerId: string) {
  const refresh = useRefreshCustomers()
  return useMutation({
    mutationFn: (input: { aliasType: 'name' | 'email'; aliasValue: string }) => customerService.addAlias(customerId, input.aliasType, input.aliasValue),
    onSuccess: refresh,
  })
}

/** Recomputing changes invoices (and may queue decisions), so the queues are refreshed by the caller. */
export function useRecomputeTds(customerId: string) {
  const refresh = useRefreshCustomers()
  return useMutation({ mutationFn: () => customerService.recomputeTds(customerId), onSuccess: refresh })
}
