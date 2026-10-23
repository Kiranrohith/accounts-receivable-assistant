import { api } from '@/lib/axios'
import type { Page } from '@/types/api'

import type {
  ActionResult, ClassifyInput, CreateResult, DisputeDetail, DisputeListItem, DisputeQuery, DraftEdits, DraftResult, NewDispute, ResolveResult, SendResult,
} from '../types/dispute'
import { toSearchParams } from '../utils/view'

export const disputeService = {
  /** One page of the dispute table: filtered, searched and sorted by the server, with the total that matches. */
  async search(query: DisputeQuery): Promise<Page<DisputeListItem>> {
    const { data } = await api.get<Page<DisputeListItem>>('/disputes/search', { params: toSearchParams(query) })
    return data
  },

  async create(input: NewDispute): Promise<CreateResult> {
    const { data } = await api.post<CreateResult>('/disputes', {
      summary: input.summary,
      invoice_id: input.invoiceId || undefined,
      customer_id: input.customerId || undefined,
      category: input.category || undefined,
      payment_related: input.paymentRelated,
      from_email: input.fromEmail || undefined,
    })
    return data
  },

  async get(disputeId: string): Promise<DisputeDetail> {
    const { data } = await api.get<DisputeDetail>(`/disputes/${disputeId}`)
    return data
  },

  async classify(disputeId: string, input: ClassifyInput): Promise<ActionResult> {
    const { data } = await api.post<ActionResult>(`/disputes/${disputeId}/classify`, {
      category: input.category,
      payment_related: input.paymentRelated,
      customer_id: input.customerId || undefined,
      invoice_id: input.invoiceId || undefined,
    })
    return data
  },

  /** The model writes a draft from our records; nothing is sent. */
  async draft(disputeId: string, instructions?: string): Promise<DraftResult> {
    const { data } = await api.post<DraftResult>(`/disputes/${disputeId}/drafts`, { instructions: instructions || undefined })
    return data
  },

  async editDraft(disputeId: string, messageId: string, edits: DraftEdits): Promise<DraftResult> {
    const { data } = await api.patch<DraftResult>(`/disputes/${disputeId}/messages/${messageId}`, edits)
    return data
  },

  async send(disputeId: string, messageId: string): Promise<SendResult> {
    const { data } = await api.post<SendResult>(`/disputes/${disputeId}/messages/${messageId}/send`)
    return data
  },

  async addNote(disputeId: string, body: string): Promise<void> {
    await api.post(`/disputes/${disputeId}/notes`, { body })
  },

  async resolve(disputeId: string, narrative: string): Promise<ResolveResult> {
    const { data } = await api.post<ResolveResult>(`/disputes/${disputeId}/resolve`, { narrative })
    return data
  },

  async reassign(disputeId: string, assignedTo: string, reason?: string): Promise<ActionResult> {
    const { data } = await api.post<ActionResult>(`/disputes/${disputeId}/reassign`, { assigned_to: assignedTo, reason: reason || undefined })
    return data
  },
}
