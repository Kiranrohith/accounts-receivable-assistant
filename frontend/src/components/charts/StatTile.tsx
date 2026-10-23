import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface StatTileProps {
  /** Sentence case, no trailing colon. */
  label: string
  value: ReactNode
  /** What the number counts or compares to, in one short line. */
  hint?: ReactNode
  /** The number in the warning ink: something is late or needs attention. Never the only signal: the hint says why. */
  alert?: boolean
  /** The single headline number of a view, large; use at most one per page. */
  hero?: boolean
  /** The full figure for the tooltip when the value is shortened. */
  title?: string
  /**
   * Makes the whole tile a link. Given the label and the class that stretches the link over the tile, it returns a typed router
   * `<Link>`, so the destination is checked where it is written: `link={(label, cls) => <Link to="/invoices" className={cls}>{label}</Link>}`.
   */
  link?: (label: string, className: string) => ReactNode
  className?: string
}

const STRETCH = 'after:absolute after:inset-0 after:content-[""] hover:underline'

/** One number with its name. Not a chart: a single current value needs no plot. Put tiles inside a `<dl>`. */
export function StatTile({ label, value, hint, alert = false, hero = false, title, link, className }: StatTileProps) {
  return (
    <div className={cn('relative rounded-lg border bg-card p-4', link && 'transition-colors hover:bg-muted/50', className)}>
      <dt className="text-xs text-muted-foreground">{link ? link(label, STRETCH) : label}</dt>
      <dd className={cn('mt-1 font-semibold tracking-tight', hero ? 'text-5xl' : 'text-2xl', alert && 'text-red-700')} title={title}>
        {value}
      </dd>
      {hint && <dd className="mt-1 text-xs text-muted-foreground">{hint}</dd>}
    </div>
  )
}
