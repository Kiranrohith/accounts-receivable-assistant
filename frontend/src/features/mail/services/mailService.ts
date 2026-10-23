import { api } from '@/lib/axios'
import type { Page } from '@/types/api'

import type { ClassifyResult, MailClass, MailLogItem, MailLogQuery, RerouteResult, TriageMail } from '../types/mail'
import { toLogParams } from '../utils/view'

export const mailService = {
  /** One page of the mail log: filtered by what became of the mail, searched and sorted by the server, with the total that matches. */
  async log(query: MailLogQuery): Promise<Page<MailLogItem>> {
    const { data } = await api.get<Page<MailLogItem>>('/mail/log', { params: toLogParams(query) })
    return data
  },

  async triageDetail(emailId: string): Promise<TriageMail> {
    const { data } = await api.get<TriageMail>(`/mail/triage/${emailId}`)
    return data
  },

  async classify(emailId: string, mailClass: MailClass, note?: string): Promise<ClassifyResult> {
    const { data } = await api.post<ClassifyResult>(`/mail/triage/${emailId}/classify`, { mail_class: mailClass, note: note || undefined })
    return data
  },

  /** A mail handed to the wrong pipeline goes to the other one: payment advice <-> complaint. */
  async reroute(emailId: string, mailClass: 'payment' | 'dispute', note?: string): Promise<RerouteResult> {
    const { data } = await api.post<RerouteResult>(`/mail/triage/${emailId}/reroute`, { mail_class: mailClass, note: note || undefined })
    return data
  },
}
