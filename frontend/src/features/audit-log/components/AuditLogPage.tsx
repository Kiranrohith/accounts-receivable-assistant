import { Link } from '@tanstack/react-router'

import { PageHeader } from '@/components/common/PageHeader'
import { PERMISSIONS } from '@/config/constants'
import { hasPermission, useCurrentUser } from '@/features/auth'
import { cn } from '@/lib/utils'

import type { AuditTab, StepResult, Trigger } from '../types/audit'
import { StateView } from './StateView'
import { StepsView } from './StepsView'

export interface AuditLogPageProps {
  tab: AuditTab
  entityType?: string | undefined
  entityId?: string | undefined
  trigger?: Trigger | undefined
  result?: StepResult | undefined
  step?: string | undefined
  job?: string | undefined
  /** The step whose details are open, from the URL. */
  open?: string | undefined
  from?: string | undefined
  to?: string | undefined
  q?: string | undefined
  sort?: string | undefined
  dir?: 'asc' | 'desc' | undefined
  page: number
  size: number
}

const TAB = 'rounded-md border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-muted'

/** What happened to records (the state log) and what each background step did (the step log). Read-only; for administrators. */
export function AuditLogPage(props: AuditLogPageProps) {
  const user = useCurrentUser()
  const canState = hasPermission(user, PERMISSIONS.logsTimelineView)
  const canSteps = hasPermission(user, PERMISSIONS.logsSteplogView)
  // someone who may read only one log sees that one, whatever the URL says
  const tab: AuditTab = props.tab === 'steps' ? (canSteps ? 'steps' : 'state') : canState ? 'state' : 'steps'
  const shared = { entityType: props.entityType, entityId: props.entityId, from: props.from, to: props.to, q: props.q, dir: props.dir, page: props.page, size: props.size }

  return (
    <>
      <PageHeader title="Audit log" description="What happened to each record, and what each background step did. Nothing here can be changed." />

      <nav aria-label="Logs" className="mb-4 flex flex-wrap gap-2">
        {canState && (
          <Link from="/audit-log" to="/audit-log" search={(prev) => ({ ...prev, tab: 'state', open: undefined, page: 1 })} aria-current={tab === 'state' ? 'page' : undefined} className={cn(TAB, tab === 'state' && 'border-primary bg-accent text-accent-foreground')}>
            State changes
          </Link>
        )}
        {canSteps && (
          <Link from="/audit-log" to="/audit-log" search={(prev) => ({ ...prev, tab: 'steps', page: 1 })} aria-current={tab === 'steps' ? 'page' : undefined} className={cn(TAB, tab === 'steps' && 'border-primary bg-accent text-accent-foreground')}>
            Step log
          </Link>
        )}
      </nav>

      {tab === 'state' ? (
        <StateView {...shared} trigger={props.trigger} />
      ) : (
        <StepsView {...shared} step={props.step} result={props.result} jobId={props.job} sort={props.sort} open={props.open} />
      )}
    </>
  )
}
