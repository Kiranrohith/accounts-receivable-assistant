import { api } from '@/lib/axios'

import type { ClassifyResult, MailClass, TriageMail } from '../types/mail'

export const mailService = {
  async triageDetail(emailId: string): Promise<TriageMail> {
    const { data } = await api.get<TriageMail>(`/mail/triage/${emailId}`)
    return data
  },

  async classify(emailId: string, mailClass: MailClass, note?: string): Promise<ClassifyResult> {
    const { data } = await api.post<ClassifyResult>(`/mail/triage/${emailId}/classify`, { mail_class: mailClass, note: note || undefined })
    return data
  },
}
