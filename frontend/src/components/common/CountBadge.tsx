import { cn } from '@/lib/utils'

interface CountBadgeProps {
  count: number
  /** Pin to the top-right corner of a relatively positioned parent (the Take Action tabs). */
  corner?: boolean
  className?: string
}

/** A small round count; nothing is drawn for zero. Counts past 99 read "99+". */
export function CountBadge({ count, corner = false, className }: CountBadgeProps) {
  if (count <= 0) return null
  return (
    <span
      aria-label={`${count} waiting`}
      className={cn(
        'inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary px-1.5 text-[11px] font-semibold leading-none text-primary-foreground',
        corner && 'absolute -top-2 -right-2',
        className,
      )}
    >
      {count > 99 ? '99+' : count}
    </span>
  )
}
