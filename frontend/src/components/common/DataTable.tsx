import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, ChevronsUpDown } from 'lucide-react'
import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { PAGE_SIZES } from '@/config/constants'
import { cn } from '@/lib/utils'

import { ErrorAlert } from './QueryStates'

export interface Column<T> {
  key: string
  header: ReactNode
  cell: (row: T) => ReactNode
  /** The sort key sent to the server; omit for a column that cannot be sorted. */
  sortKey?: string
  align?: 'left' | 'right'
  className?: string
}

export interface SortState {
  key: string
  dir: 'asc' | 'desc'
}

interface DataTableProps<T> {
  columns: Column<T>[]
  /** Undefined while the first page loads. */
  rows: T[] | undefined
  total: number
  rowKey: (row: T) => string
  page: number
  pageSize: number
  onPageChange: (page: number) => void
  onPageSizeChange: (size: number) => void
  sort?: SortState
  onSortChange?: (sort: SortState) => void
  /** The search box and filter controls, rendered above the table. */
  toolbar?: ReactNode
  isPending?: boolean
  error?: unknown
  onRetry?: () => void
  /** Shown when the server returns no rows: one sentence plus the action that fills the list. */
  empty: ReactNode
  onRowClick?: (row: T) => void
  selectedKey?: string
  rowClassName?: (row: T) => string | undefined
}

const SKELETON_ROWS = 6

/**
 * Renders rows and controls only. Paging, sorting and searching all happen on the server: this component never
 * sorts, filters or slices what it is given.
 */
export function DataTable<T>({
  columns, rows, total, rowKey, page, pageSize, onPageChange, onPageSizeChange, sort, onSortChange, toolbar,
  isPending = false, error, onRetry, empty, onRowClick, selectedKey, rowClassName,
}: DataTableProps<T>) {
  const lastPage = Math.max(1, Math.ceil(total / pageSize))
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1
  const last = Math.min(total, page * pageSize)

  function toggleSort(sortKey: string) {
    if (!onSortChange) return
    const sameColumn = sort?.key === sortKey
    onSortChange({ key: sortKey, dir: sameColumn && sort?.dir === 'asc' ? 'desc' : 'asc' })
  }

  return (
    <div className="rounded-lg border bg-card">
      {toolbar && <div className="flex flex-wrap items-center gap-3 border-b p-3">{toolbar}</div>}

      {error ? (
        <div className="p-4">
          <ErrorAlert error={error} onRetry={onRetry} />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/50 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground">
              <tr>
                {columns.map((column) => {
                  const active = column.sortKey !== undefined && sort?.key === column.sortKey
                  const ariaSort = active ? (sort?.dir === 'asc' ? 'ascending' : 'descending') : undefined
                  return (
                    <th key={column.key} scope="col" aria-sort={ariaSort} className={cn('px-3 py-2.5', column.align === 'right' && 'text-right', column.className)}>
                      {column.sortKey ? (
                        <button
                          type="button"
                          onClick={() => toggleSort(column.sortKey!)}
                          className={cn('inline-flex items-center gap-1 uppercase hover:text-foreground', active && 'text-foreground')}
                        >
                          {column.header}
                          {active ? sort?.dir === 'asc' ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" /> : <ChevronsUpDown className="size-3 opacity-50" />}
                        </button>
                      ) : (
                        column.header
                      )}
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {isPending || rows === undefined
                ? Array.from({ length: SKELETON_ROWS }, (_, i) => (
                    <tr key={i} className="border-b last:border-0" aria-hidden>
                      {columns.map((column) => (
                        <td key={column.key} className="px-3 py-3">
                          <Skeleton className="h-4 w-full" />
                        </td>
                      ))}
                    </tr>
                  ))
                : rows.map((row) => {
                    const key = rowKey(row)
                    return (
                      <tr
                        key={key}
                        onClick={onRowClick ? () => onRowClick(row) : undefined}
                        onKeyDown={onRowClick ? (e) => (e.key === 'Enter' || e.key === ' ') && onRowClick(row) : undefined}
                        tabIndex={onRowClick ? 0 : undefined}
                        aria-selected={selectedKey === undefined ? undefined : selectedKey === key}
                        className={cn(
                          'border-b last:border-0',
                          onRowClick && 'cursor-pointer hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none',
                          selectedKey === key && 'bg-accent',
                          rowClassName?.(row),
                        )}
                      >
                        {columns.map((column) => (
                          <td key={column.key} className={cn('px-3 py-3 align-middle', column.align === 'right' && 'text-right tabular-nums', column.className)}>
                            {column.cell(row)}
                          </td>
                        ))}
                      </tr>
                    )
                  })}
            </tbody>
          </table>
          {!isPending && rows !== undefined && rows.length === 0 && empty}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-3 py-2 text-sm text-muted-foreground">
        <span>{total === 0 ? 'No results' : `Showing ${first}–${last} of ${total}`}</span>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2">
            Rows
            <Select value={String(pageSize)} onValueChange={(v) => onPageSizeChange(Number(v))}>
              <SelectTrigger size="sm" className="w-[70px]" aria-label="Rows per page">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PAGE_SIZES.map((size) => (
                  <SelectItem key={size} value={String(size)}>
                    {size}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <span aria-live="polite">
            Page {page} of {lastPage}
          </span>
          <div className="flex gap-1">
            <Button variant="outline" size="icon-sm" aria-label="Previous page" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
              <ChevronLeft />
            </Button>
            <Button variant="outline" size="icon-sm" aria-label="Next page" disabled={page >= lastPage} onClick={() => onPageChange(page + 1)}>
              <ChevronRight />
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
