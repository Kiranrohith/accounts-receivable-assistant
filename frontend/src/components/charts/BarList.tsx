import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

export interface BarItem {
  key: string
  /** The row's name, in text ink; identity comes from where the row sits, not from colour. */
  label: ReactNode
  value: number
  /** What to print at the tip of the bar. */
  valueLabel: string
  /** A second line under the label (counts, a share) and the hover tooltip. */
  note?: string
  /** Draw this bar in the muted ink instead of the accent, for the rows that are context rather than the point. */
  muted?: boolean
}

interface BarListProps {
  items: BarItem[]
  /** Names the list for assistive technology: "Amount owed by days late". */
  label: string
  /** Shown instead of the bars when every value is zero. */
  empty?: string
  className?: string
}

/** The share of the track a full-length bar uses; the rest is left for the value at its tip. */
const TRACK = 0.78

/**
 * Horizontal bars for comparing magnitudes: one hue, thin (20px at most), square at the baseline and rounded at the data end,
 * the value at the tip and the name in text ink. Every row is a list item with its figures in text, so the chart reads without colour.
 */
export function BarList({ items, label, empty = 'Nothing to show yet.', className }: BarListProps) {
  const max = Math.max(...items.map((i) => i.value), 0)
  if (max <= 0) return <p className="py-6 text-center text-sm text-muted-foreground">{empty}</p>

  return (
    <ul aria-label={label} className={cn('space-y-2', className)}>
      {items.map((item) => {
        const width = (item.value / max) * TRACK * 100
        return (
          <li key={item.key} title={item.note ? `${typeof item.label === 'string' ? item.label : ''} ${item.valueLabel} · ${item.note}`.trim() : undefined} className="grid grid-cols-[minmax(7rem,11rem)_1fr] items-center gap-3 text-sm">
            <span className="min-w-0 text-foreground">
              <span className="block truncate">{item.label}</span>
              {item.note && <span className="block truncate text-xs text-muted-foreground">{item.note}</span>}
            </span>
            <span className="relative block h-5">
              <span
                aria-hidden
                className={cn('absolute inset-y-0 left-0 rounded-r-[4px]', item.muted ? 'bg-[var(--viz-muted)]' : 'bg-[var(--viz-1)]')}
                style={{ width: `${Math.max(width, item.value > 0 ? 0.5 : 0)}%` }}
              />
              <span className="absolute top-1/2 -translate-y-1/2 whitespace-nowrap font-medium tabular-nums text-foreground" style={{ left: `calc(${width}% + 8px)` }}>
                {item.valueLabel}
              </span>
            </span>
          </li>
        )
      })}
    </ul>
  )
}
