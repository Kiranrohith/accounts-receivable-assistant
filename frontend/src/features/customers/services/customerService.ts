import { api } from '@/lib/axios'

import type { Customer, CustomerDetail, CustomerEdits, NewCustomer, TdsRecomputeResult } from '../types/customer'

export const customerService = {
  /** Active customers, alphabetical, each with its open/overdue/outstanding/unapplied figures. */
  async list(): Promise<Customer[]> {
    const { data } = await api.get<Customer[]>('/customers')
    return data
  },

  async get(customerId: string): Promise<CustomerDetail> {
    const { data } = await api.get<CustomerDetail>(`/customers/${customerId}`)
    return data
  },

  async create(input: NewCustomer): Promise<Customer> {
    const { data } = await api.post<Customer>('/customers', input)
    return data
  },

  async update(customerId: string, edits: CustomerEdits): Promise<CustomerDetail> {
    const { data } = await api.patch<CustomerDetail>(`/customers/${customerId}`, edits)
    return data
  },

  async addAlias(customerId: string, aliasType: 'name' | 'email', aliasValue: string): Promise<CustomerDetail> {
    const { data } = await api.post<CustomerDetail>(`/customers/${customerId}/aliases`, { alias_type: aliasType, alias_value: aliasValue })
    return data
  },

  async recomputeTds(customerId: string): Promise<TdsRecomputeResult> {
    const { data } = await api.post<TdsRecomputeResult>(`/customers/${customerId}/recompute-tds`)
    return data
  },
}
