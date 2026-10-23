/** Public API of the mail feature. */
export { MailLogPage } from './components/MailLogPage'
export { mailKeys, mailLogQuery, triageMailQuery, useClassifyMail } from './queries/mailQueries'
export { MAIL_OUTCOMES } from './types/mail'
export type { ClassifyResult, MailAttachment, MailClass, MailLogItem, MailOutcome, TriageMail } from './types/mail'
