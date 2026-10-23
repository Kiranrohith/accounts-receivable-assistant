import { queryOptions, useMutation, useQueryClient } from '@tanstack/react-query'

import { disputeService } from '../services/disputeService'
import type { ClassifyInput, DraftEdits } from '../types/dispute'

export const disputeKeys = {
  all: ['disputes'] as const,
  detail: (disputeId: string) => [...disputeKeys.all, 'detail', disputeId] as const,
}

export const disputeQuery = (disputeId: string) =>
  queryOptions({
    queryKey: disputeKeys.detail(disputeId),
    queryFn: () => disputeService.get(disputeId),
  })

/** Whatever is done to a dispute changes its thread, status or owner, so it is read again. */
function useReadAgain(disputeId: string) {
  const client = useQueryClient()
  return () => client.invalidateQueries({ queryKey: disputeKeys.detail(disputeId) })
}

export function useClassifyDispute(disputeId: string) {
  return useMutation({ mutationFn: (input: ClassifyInput) => disputeService.classify(disputeId, input) })
}

export function useDraftEmail(disputeId: string) {
  const again = useReadAgain(disputeId)
  return useMutation({ mutationFn: (instructions?: string) => disputeService.draft(disputeId, instructions), onSuccess: again })
}

export function useEditDraft(disputeId: string) {
  const again = useReadAgain(disputeId)
  return useMutation({ mutationFn: (input: { messageId: string; edits: DraftEdits }) => disputeService.editDraft(disputeId, input.messageId, input.edits), onSuccess: again })
}

export function useSendDraft(disputeId: string) {
  const again = useReadAgain(disputeId)
  return useMutation({ mutationFn: (messageId: string) => disputeService.send(disputeId, messageId), onSuccess: again })
}

export function useAddNote(disputeId: string) {
  const again = useReadAgain(disputeId)
  return useMutation({ mutationFn: (body: string) => disputeService.addNote(disputeId, body), onSuccess: again })
}

export function useResolveDispute(disputeId: string) {
  const again = useReadAgain(disputeId)
  return useMutation({ mutationFn: (narrative: string) => disputeService.resolve(disputeId, narrative), onSuccess: again })
}

export function useReassignDispute(disputeId: string) {
  const again = useReadAgain(disputeId)
  return useMutation({ mutationFn: (input: { assignedTo: string; reason?: string }) => disputeService.reassign(disputeId, input.assignedTo, input.reason), onSuccess: again })
}
