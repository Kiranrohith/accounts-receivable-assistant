/** App-wide static values. Anything the backend owns (thresholds, schedules) is read from the API, never copied here. */

export const APP_NAME = 'Genworx AR'
export const APP_TIME_ZONE = 'Asia/Kolkata'

export const PAGE_SIZES = [25, 50, 100] as const
export const DEFAULT_PAGE_SIZE = 25

export const UPLOAD_POLL_MS = 5_000
export const NOTIFICATION_POLL_MS = 60_000

/** Permission codes the UI hides or shows things by (enforcement stays on the backend). */
export const PERMISSIONS = {
  invoicesUpload: 'invoices.upload',
  invoicesView: 'invoices.view',
  invoicesReview: 'invoices.review',
  invoicesRejectedRerun: 'invoices.rejected.rerun',
  customersView: 'customers.view',
  customersManage: 'customers.manage',
  customersAliasConfirm: 'customers.alias.confirm',
  paymentsView: 'payments.view',
  paymentsReviewMatch: 'payments.review.match',
  paymentsCorrectionRerun: 'payments.correction.rerun',
  paymentsPoOverride: 'payments.po_override',
  notesCreate: 'notes.create',
  mailTriageClassify: 'mail.triage.classify',
  remindersManage: 'reminders.manage',
  remindersViewSent: 'reminders.view_sent',
  tasksCollectionWork: 'tasks.collection.work',
  tasksRetromatchReview: 'tasks.retromatch.review',
  disputesDashboardView: 'disputes.dashboard.view',
  disputesClassify: 'disputes.classify',
  disputesWorkNormal: 'disputes.work.normal',
  disputesWorkEscalated: 'disputes.work.escalated',
  disputesReassign: 'disputes.reassign',
  reportsView: 'reports.view',
  failedItemsRerun: 'failed_items.rerun',
  usersManage: 'users.manage',
  logsTimelineView: 'logs.timeline.view',
  logsSteplogView: 'logs.steplog.view',
} as const

export type PermissionCode = (typeof PERMISSIONS)[keyof typeof PERMISSIONS]
