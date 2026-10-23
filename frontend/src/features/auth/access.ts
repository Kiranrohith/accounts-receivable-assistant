import { PERMISSIONS as P, type PermissionCode } from '@/config/constants'

/**
 * Who may open each screen: holding ANY of the listed permissions is enough; an empty list means every signed-in user.
 * The sidebar and the route guards both read this, so a screen can never be shown in one and refused in the other.
 * The backend still enforces every action; this only decides what the UI shows.
 */
export const ACCESS = {
  dashboard: [],
  myWork: [],
  takeAction: [
    P.invoicesReview, P.invoicesRejectedRerun, P.paymentsReviewMatch, P.mailTriageClassify, P.remindersManage,
    P.disputesClassify, P.disputesWorkNormal, P.disputesWorkEscalated, P.tasksCollectionWork,
    P.tasksRetromatchReview, P.failedItemsRerun, P.paymentsPoOverride,
  ],
  upload: [P.invoicesUpload],
  invoices: [P.invoicesView],
  purchaseOrders: [P.invoicesView],
  payments: [P.paymentsView],
  disputes: [P.disputesDashboardView],
  reminders: [P.remindersManage, P.remindersViewSent],
  customers: [P.customersView],
  reports: [P.reportsView],
  mail: [P.mailTriageClassify, P.usersManage],
  users: [P.usersManage],
  settings: [P.usersManage],
  profile: [],
} as const satisfies Record<string, readonly PermissionCode[]>

export type AccessKey = keyof typeof ACCESS
