import type { BarItem } from '@/components/charts/BarList'
import type { CountBy } from '@/features/disputes'
import { formatCompactInr } from '@/utils/format'

import type { AgingBucket } from '../types/dashboard'

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

/**
 * The aging bands as bars: what is owed in each, how many invoices that is and how many of them are disputed. Money that is not yet
 * due is drawn in the muted ink: it is context, and the late bands are the point.
 */
export function agingBars(aging: AgingBucket[]): BarItem[] {
  return aging.map((b) => ({
    key: b.key,
    label: b.label,
    value: Number(b.amount),
    valueLabel: formatCompactInr(b.amount),
    note: `${plural(b.count, 'invoice')}${b.disputed_count > 0 ? `, ${b.disputed_count} disputed` : ''}`,
    muted: b.key === 'current',
  }))
}

/** Disputes by reason, the busiest first: the open ones are the bar, the all-time count is the note. */
export function reasonBars(rows: CountBy[], label: (key: string) => string): BarItem[] {
  return rows
    .filter((r) => r.total > 0)
    .map((r) => ({ key: r.key, label: label(r.key), value: r.open, valueLabel: String(r.open), note: `${r.total} in all`, muted: r.open === 0 }))
}

/** Open disputes by how long ago they were raised, in the order the server gives (youngest first). */
export function ageBars(rows: CountBy[]): BarItem[] {
  return rows.map((r) => ({ key: r.key, label: r.key, value: r.open, valueLabel: String(r.open) }))
}

const SHORT_DAY = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'Asia/Kolkata' })

/** 2026-10-23 as "23 Oct". */
export function shortDay(day: string): string {
  return SHORT_DAY.format(new Date(`${day}T12:00:00+05:30`))
}
