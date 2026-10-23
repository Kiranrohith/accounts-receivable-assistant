import { api } from '@/lib/axios'
import type { Page } from '@/types/api'

import type { LogFacets, StateEntry, StateQuery, StepDetail, StepEntry, StepsQuery } from '../types/audit'
import { toStateParams, toStepParams } from '../utils/view'

export const auditService = {
  async state(query: StateQuery): Promise<Page<StateEntry>> {
    const { data } = await api.get<Page<StateEntry>>('/logs/state', { params: toStateParams(query) })
    return data
  },

  async steps(query: StepsQuery): Promise<Page<StepEntry>> {
    const { data } = await api.get<Page<StepEntry>>('/logs/steps', { params: toStepParams(query) })
    return data
  },

  async step(stepId: string): Promise<StepDetail> {
    const { data } = await api.get<StepDetail>(`/logs/steps/${stepId}`)
    return data
  },

  async facets(): Promise<LogFacets> {
    const { data } = await api.get<LogFacets>('/logs/facets')
    return data
  },
}
