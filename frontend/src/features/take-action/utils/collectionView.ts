import type { TaskItem } from '@/features/tasks'

export const TASK_SORTS = ['invoice', 'customer', 'assigned', 'overdue', 'owed', 'handed'] as const
export type TaskSort = (typeof TASK_SORTS)[number]

export interface TaskView {
  q?: string
  sort?: string
  dir?: 'asc' | 'desc'
  /** 'me' keeps the tasks assigned to this user; 'all' every task. */
  who: 'all' | 'me'
  userId: string | undefined
  page: number
  size: number
}

const SORT_VALUE: Record<TaskSort, (t: TaskItem) => string | number> = {
  invoice: (t) => t.invoice_number ?? '',
  customer: (t) => t.customer_name ?? '',
  assigned: (t) => t.assigned_to_name ?? '',
  overdue: (t) => t.days_overdue ?? -1,
  owed: (t) => Number(t.balance ?? 0),
  handed: (t) => t.created_at,
}

const compare = (a: string | number, b: string | number): number =>
  typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b), undefined, { sensitivity: 'base' })

/**
 * The tasks arrive whole (a few hundred at most, not paged by the server), so the owner filter, search, sort and paging are
 * applied here, from the same URL parameters every other list uses. Most overdue first unless a sort is chosen.
 */
export function viewTasks(all: TaskItem[], view: TaskView): { rows: TaskItem[]; total: number } {
  const needle = (view.q ?? '').trim().toLowerCase()
  const mine = view.who === 'me' ? all.filter((t) => t.assigned_to !== null && t.assigned_to === view.userId) : all
  const matching = needle
    ? mine.filter((t) => [t.invoice_number, t.customer_name, t.assigned_to_name].some((field) => (field ?? '').toLowerCase().includes(needle)))
    : mine

  const chosen = (TASK_SORTS as readonly string[]).includes(view.sort ?? '')
  const key: TaskSort = chosen ? (view.sort as TaskSort) : 'overdue'
  const factor = chosen ? (view.dir === 'desc' ? -1 : 1) : -1
  const sorted = [...matching].sort((a, b) => factor * compare(SORT_VALUE[key](a), SORT_VALUE[key](b)) || (a.invoice_number ?? '').localeCompare(b.invoice_number ?? ''))

  const start = (view.page - 1) * view.size
  return { rows: sorted.slice(start, start + view.size), total: sorted.length }
}
