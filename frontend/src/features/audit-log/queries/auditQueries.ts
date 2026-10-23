import { keepPreviousData, queryOptions } from '@tanstack/react-query'

import { auditService } from '../services/auditService'
import type { StateQuery, StepsQuery } from '../types/audit'

export const auditKeys = {
  all: ['audit-log'] as const,
  state: (query: StateQuery) => [...auditKeys.all, 'state', query] as const,
  steps: (query: StepsQuery) => [...auditKeys.all, 'steps', query] as const,
  step: (stepId: string) => [...auditKeys.all, 'step', stepId] as const,
  facets: () => [...auditKeys.all, 'facets'] as const,
}

/** One page of the state log; the old page stays on screen while the next one loads. `enabled` is off while the window is the wrong way round. */
export const stateLogQuery = (query: StateQuery, enabled = true) =>
  queryOptions({ queryKey: auditKeys.state(query), queryFn: () => auditService.state(query), placeholderData: keepPreviousData, enabled })

export const stepLogQuery = (query: StepsQuery, enabled = true) =>
  queryOptions({ queryKey: auditKeys.steps(query), queryFn: () => auditService.steps(query), placeholderData: keepPreviousData, enabled })

export const stepDetailQuery = (stepId: string) => queryOptions({ queryKey: auditKeys.step(stepId), queryFn: () => auditService.step(stepId) })

/** The kinds, step names, results and triggers the filters offer; they change rarely. */
export const facetsQuery = () => queryOptions({ queryKey: auditKeys.facets(), queryFn: () => auditService.facets(), staleTime: 5 * 60 * 1000 })
