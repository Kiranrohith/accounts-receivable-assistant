import { keepPreviousData, queryOptions, useMutation, useQueryClient } from '@tanstack/react-query'

import { mailService } from '../services/mailService'
import type { MailClass, MailLogQuery } from '../types/mail'
import { LIVE_POLL_MS } from '../utils/live'

export const mailKeys = {
  all: ['mail'] as const,
  log: (query: MailLogQuery) => [...mailKeys.all, 'log', query] as const,
  live: () => [...mailKeys.all, 'live'] as const,
  triage: (emailId: string) => [...mailKeys.all, 'triage', emailId] as const,
}

/** One page of the mail log; the old page stays on screen while the next one loads. */
export const mailLogQuery = (query: MailLogQuery) =>
  queryOptions({ queryKey: mailKeys.log(query), queryFn: () => mailService.log(query), placeholderData: keepPreviousData })

/** The mail being processed. It asks again every couple of seconds (and stops while the tab is in the background). */
export const liveMailQuery = () =>
  queryOptions({ queryKey: mailKeys.live(), queryFn: () => mailService.live(), refetchInterval: LIVE_POLL_MS, placeholderData: keepPreviousData })

export const triageMailQuery = (emailId: string) =>
  queryOptions({
    queryKey: mailKeys.triage(emailId),
    queryFn: () => mailService.triageDetail(emailId),
  })

export function useClassifyMail(emailId: string) {
  return useMutation({ mutationFn: (input: { mailClass: MailClass; note?: string }) => mailService.classify(emailId, input.mailClass, input.note) })
}

/** Moving a mail changes what it produced and which queues have work, so the log and the queues are read again. */
function useRefreshAfterMove() {
  const client = useQueryClient()
  return () => Promise.all(['mail', 'queues', 'payments', 'disputes'].map((key) => client.invalidateQueries({ queryKey: [key] })))
}

/** Route a mail that was set aside (or is waiting) from the log. */
export function useRouteMailFromLog(emailId: string) {
  const refresh = useRefreshAfterMove()
  return useMutation({ mutationFn: (input: { mailClass: MailClass; note?: string }) => mailService.classify(emailId, input.mailClass, input.note), onSuccess: refresh })
}

export function useRerouteMail(emailId: string) {
  const refresh = useRefreshAfterMove()
  return useMutation({ mutationFn: (input: { mailClass: 'payment' | 'dispute'; note?: string }) => mailService.reroute(emailId, input.mailClass, input.note), onSuccess: refresh })
}
