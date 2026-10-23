import { cn } from '@/lib/utils'

type Tone = 'teal' | 'amber' | 'green' | 'red' | 'gray' | 'orange' | 'blue' | 'violet' | 'purple'

// Full class strings, never built by concatenation, so Tailwind can see them.
const TONES: Record<Tone, { chip: string; dot: string }> = {
  teal: { chip: 'bg-teal-50 text-teal-800 ring-teal-200', dot: 'bg-teal-600' },
  amber: { chip: 'bg-amber-50 text-amber-800 ring-amber-200', dot: 'bg-amber-500' },
  green: { chip: 'bg-green-50 text-green-800 ring-green-200', dot: 'bg-green-600' },
  red: { chip: 'bg-red-50 text-red-800 ring-red-200', dot: 'bg-red-600' },
  gray: { chip: 'bg-slate-100 text-slate-700 ring-slate-200', dot: 'bg-slate-500' },
  orange: { chip: 'bg-orange-50 text-orange-800 ring-orange-200', dot: 'bg-orange-500' },
  blue: { chip: 'bg-blue-50 text-blue-800 ring-blue-200', dot: 'bg-blue-600' },
  violet: { chip: 'bg-violet-50 text-violet-800 ring-violet-200', dot: 'bg-violet-600' },
  purple: { chip: 'bg-purple-50 text-purple-800 ring-purple-200', dot: 'bg-purple-600' },
}

/**
 * The single source of truth for status colours (design section 17). A status the map does not know reads as gray
 * with its own text, so a new backend state is visible at once instead of silently mis-coloured.
 */
const STATUS_TONE: Record<string, Tone> = {
  Open: 'teal',
  PartlyPaid: 'amber',
  Paid: 'green',
  Overdue: 'red',
  Rejected: 'gray',
  NeedsReview: 'orange',
  Unapplied: 'blue',
  Held: 'violet',
  Disputed: 'purple',
  'PO-hold': 'orange',
  Failed: 'red',
  // payment states
  Applied: 'green',
  PartiallyApplied: 'amber',
  // disputes
  Raised: 'amber',
  Categorised: 'teal',
  ClarificationSent: 'blue',
  Assigned: 'teal',
  Escalated: 'red',
  Resolved: 'green',
  // an upload's life, as the Upload page words it
  Uploading: 'gray',
  Uploaded: 'blue',
  Processing: 'blue',
  Completed: 'green',
  'Needs review': 'orange',
  Duplicate: 'gray',
  // reminders
  Drafted: 'amber',
  Sent: 'green',
  SendFailed: 'red',
  Cancelled: 'gray',
  // purchase order fulfilment
  'Not invoiced': 'gray',
  'Partly invoiced': 'amber',
  'Fully invoiced': 'green',
  'Over-invoiced': 'red',
  // reminder tiers
  Gentle: 'blue',
  Firm: 'amber',
  Final: 'red',
}

export function toneOf(status: string): Tone {
  return STATUS_TONE[status] ?? 'gray'
}

interface StatusChipProps {
  status: string
  /** Text to show when it should read differently from the stored state ("Partly paid"). */
  label?: string
  /** A dot only, for the invoice table's disputed and PO-hold markers; the label becomes its tooltip. */
  dot?: boolean
  className?: string
}

export function StatusChip({ status, label, dot = false, className }: StatusChipProps) {
  const tone = TONES[toneOf(status)]
  const text = label ?? status
  if (dot) {
    return <span role="img" aria-label={text} title={text} className={cn('inline-block size-2.5 rounded-full', tone.dot, className)} />
  }
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset', tone.chip, className)}>
      <span className={cn('size-1.5 rounded-full', tone.dot)} aria-hidden />
      {text}
    </span>
  )
}
