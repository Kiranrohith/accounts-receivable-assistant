import { keepPreviousData, queryOptions, useMutation, useQueryClient } from '@tanstack/react-query'

import { disputeService } from '../services/disputeService'
import type { ClassifyInput, DisputeQuery, DraftEdits } from '../types/dispute'

export const disputeKeys = {
  all: ['disputes'] as const,
  dashboard: (days: number) => [...disputeKeys.all, 'dashboard', days] as const,
  list: (query: DisputeQuery) => [...disputeKeys.all, 'list', query] as const,
  detail: (disputeId: string) => [...disputeKeys.all, 'detail', disputeId] as const,
}

/** One page of the dispute table; the old page stays on screen while the next one loads. */
export const disputeListQuery = (query: DisputeQuery) =>
  queryOptions({
    queryKey: disputeKeys.list(query),
    queryFn: () => disputeService.search(query),
    placeholderData: keepPreviousData,
  })

export const disputeDashboardQuery = (days = 30) =>
  queryOptions({
    queryKey: disputeKeys.dashboard(days),
    queryFn: () => disputeService.dashboard(days),
    staleTime: 60_000,
  })

export const disputeQuery = (disputeId: string) =>
  queryOptions({
    queryKey: disputeKeys.detail(disputeId),
    queryFn: () => disputeService.get(disputeId),
  })

/** Whatever is done to a dispute changes its thread, status or owner, so it and every list that shows it are read again. */
function useReadAgain() {
  const client = useQueryClient()
  return () => client.invalidateQueries({ queryKey: disputeKeys.all })
}

export function useCreateDispute() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: disputeService.create,
    onSuccess: () => Promise.all(['disputes', 'queues', 'invoices'].map((key) => client.invalidateQueries({ queryKey: [key] }))),
  })
}

export function useClassifyDispute(disputeId: string) {
  return useMutation({ mutationFn: (input: ClassifyInput) => disputeService.classify(disputeId, input) })
}

export function useDraftEmail(disputeId: string) {
  const again = useReadAgain()
  return useMutation({ mutationFn: (instructions?: string) => disputeService.draft(disputeId, instructions), onSuccess: again })
}

export function useEditDraft(disputeId: string) {
  const again = useReadAgain()
  return useMutation({ mutationFn: (input: { messageId: string; edits: DraftEdits }) => disputeService.editDraft(disputeId, input.messageId, input.edits), onSuccess: again })
}

export function useSendDraft(disputeId: string) {
  const again = useReadAgain()
  return useMutation({ mutationFn: (messageId: string) => disputeService.send(disputeId, messageId), onSuccess: again })
}

export function useAddNote(disputeId: string) {
  const again = useReadAgain()
  return useMutation({ mutationFn: (body: string) => disputeService.addNote(disputeId, body), onSuccess: again })
}

export function useResolveDispute(disputeId: string) {
  const again = useReadAgain()
  return useMutation({ mutationFn: (narrative: string) => disputeService.resolve(disputeId, narrative), onSuccess: again })
}

export function useReassignDispute(disputeId: string) {
  const again = useReadAgain()
  return useMutation({ mutationFn: (input: { assignedTo: string; reason?: string }) => disputeService.reassign(disputeId, input.assignedTo, input.reason), onSuccess: again })
}
