import { queryOptions, useMutation } from '@tanstack/react-query'

import { mailService } from '../services/mailService'
import type { MailClass } from '../types/mail'

export const mailKeys = {
  all: ['mail'] as const,
  triage: (emailId: string) => [...mailKeys.all, 'triage', emailId] as const,
}

export const triageMailQuery = (emailId: string) =>
  queryOptions({
    queryKey: mailKeys.triage(emailId),
    queryFn: () => mailService.triageDetail(emailId),
  })

export function useClassifyMail(emailId: string) {
  return useMutation({ mutationFn: (input: { mailClass: MailClass; note?: string }) => mailService.classify(emailId, input.mailClass, input.note) })
}
