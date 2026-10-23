import { APP_TIME_ZONE } from '@/config/constants'

import type { ReminderItem } from '../types/reminder'

export const TIER_NAME: Record<number, string> = { 1: 'Gentle', 15: 'Firm', 30: 'Final' }
export const tierName = (tier: number): string => TIER_NAME[tier] ?? `${tier} days`

export const REMINDER_SORTS = ['date', 'days', 'invoice'] as const

export interface ReminderView {
  q?: string
  /** 1, 15 or 30: only that tier. */
  tier?: number
  /** Bounds on the list's date (drafted at, or sent at), as "YYYY-MM-DD" in IST. */
  from?: string
  to?: string
  sort?: string
  dir?: 'asc' | 'desc'
  page: number
  size: number
}

const istDay = new Intl.DateTimeFormat('en-CA', { timeZone: APP_TIME_ZONE })

/** The calendar day (IST) a moment falls on, as "YYYY-MM-DD". */
export const dayOf = (iso: string): string => istDay.format(new Date(iso))

/**
 * The reminders arrive as one list per tab (the server does not total them), so search, filters, sort and paging are
 * applied here. `dateOf` says which moment the date filter and the default order use: drafted at, or sent at.
 */
export function viewReminders(all: ReminderItem[], view: ReminderView, dateOf: (r: ReminderItem) => string): { rows: ReminderItem[]; total: number } {
  const needle = (view.q ?? '').trim().toLowerCase()
  const matching = all.filter((r) => {
    if (needle && ![r.invoice_number, r.customer_name, r.recipient, r.reference].some((f) => (f ?? '').toLowerCase().includes(needle))) return false
    if (view.tier !== undefined && r.tier !== view.tier) return false
    const day = dayOf(dateOf(r))
    if (view.from && day < view.from) return false
    if (view.to && day > view.to) return false
    return true
  })

  const key = (REMINDER_SORTS as readonly string[]).includes(view.sort ?? '') ? view.sort : 'date'
  const factor = (view.dir ?? (key === 'date' ? 'desc' : 'asc')) === 'desc' ? -1 : 1
  const value = (r: ReminderItem): string | number =>
    key === 'days' ? (r.days_overdue ?? -1) : key === 'invoice' ? r.invoice_number : new Date(dateOf(r)).getTime()
  const sorted = [...matching].sort((a, b) => {
    const x = value(a), y = value(b)
    const order = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true })
    return factor * order
  })

  const start = (view.page - 1) * view.size
  return { rows: sorted.slice(start, start + view.size), total: sorted.length }
}
