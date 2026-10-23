import { APP_TIME_ZONE } from '@/config/constants'

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/

/** Money arrives as a decimal string (the backend never sends floats for money); accept a number too. */
export function formatCurrency(amount: string | number | null | undefined, currency = 'INR'): string {
  if (amount === null || amount === undefined || amount === '') return '—'
  const value = typeof amount === 'number' ? amount : Number(amount)
  if (Number.isNaN(value)) return '—'
  if (currency === 'INR') {
    return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(value)
  }
  // Other currencies read as "USD 1,234.00": the code, not a symbol that two countries share.
  return new Intl.NumberFormat('en-US', { style: 'currency', currency, currencyDisplay: 'code' })
    .format(value)
    .replace(/ /g, ' ')
}

/**
 * A rupee amount short enough for a tile: 1.2 Cr, 4.5 L (lakh), 12,300; the full figure belongs in a tooltip. Indian grouping, as
 * `formatCurrency` uses, and no paise.
 */
export function formatCompactInr(amount: string | number | null | undefined): string {
  if (amount === null || amount === undefined || amount === '') return '—'
  const value = typeof amount === 'number' ? amount : Number(amount)
  if (Number.isNaN(value)) return '—'
  const sign = value < 0 ? '-' : ''
  const abs = Math.abs(value)
  const trim = (n: number) => String(Math.round(n * 100) / 100)
  if (abs >= 1e7) return `${sign}₹${trim(abs / 1e7)} Cr`
  if (abs >= 1e5) return `${sign}₹${trim(abs / 1e5)} L`
  return `${sign}₹${new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(abs)}`
}

function parse(value: string | Date): Date {
  // A bare date (a due date) has no time zone: read it as that calendar day, never shift it.
  if (typeof value === 'string' && DATE_ONLY.test(value)) return new Date(`${value}T12:00:00+05:30`)
  return typeof value === 'string' ? new Date(value) : value
}

const dateFormat = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: APP_TIME_ZONE })
const timeFormat = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: APP_TIME_ZONE })

/** 12 Mar 2026, in IST. */
export function formatDate(value: string | Date | null | undefined): string {
  if (!value) return '—'
  const date = parse(value)
  return Number.isNaN(date.getTime()) ? '—' : dateFormat.format(date)
}

/** 12 Mar 2026, 14:05, in IST. */
export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return '—'
  const date = parse(value)
  return Number.isNaN(date.getTime()) ? '—' : `${dateFormat.format(date)}, ${timeFormat.format(date)}`
}

/** "3 hours ago" style age for queue rows. */
export function formatAge(value: string | Date | null | undefined, now: Date = new Date()): string {
  if (!value) return '—'
  const seconds = Math.max(0, Math.floor((now.getTime() - parse(value).getTime()) / 1000))
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.floor(hours / 24)
  return `${days} d ago`
}

export function initials(name: string): string {
  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join('') || '?'
  )
}
