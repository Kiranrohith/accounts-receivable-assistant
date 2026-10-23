import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { renderWithClient } from '@/test/render'

import { DataTable, type Column } from './DataTable'

interface Row {
  id: string
  name: string
}

const COLUMNS: Column<Row>[] = [
  { key: 'name', header: 'Name', cell: (r) => r.name, sortKey: 'name' },
]

const base = {
  columns: COLUMNS,
  total: 60,
  rowKey: (r: Row) => r.id,
  page: 2,
  pageSize: 25,
  onPageChange: () => {},
  onPageSizeChange: () => {},
  empty: <p>Nothing here</p>,
}

describe('DataTable', () => {
  it('renders the rows it is given, untouched, and says where the page sits', () => {
    renderWithClient(<DataTable {...base} rows={[{ id: '1', name: 'Zed' }, { id: '2', name: 'Abe' }]} />)
    const cells = screen.getAllByRole('cell').map((c) => c.textContent)
    expect(cells).toEqual(['Zed', 'Abe']) // never re-sorted in the browser
    expect(screen.getByText('Showing 26–50 of 60')).toBeInTheDocument()
    expect(screen.getByText('Page 2 of 3')).toBeInTheDocument()
  })

  it('asks the parent to sort and flips direction on a second click', async () => {
    const onSortChange = vi.fn()
    renderWithClient(<DataTable {...base} rows={[]} onSortChange={onSortChange} />)
    await userEvent.click(screen.getByRole('button', { name: /name/i }))
    expect(onSortChange).toHaveBeenLastCalledWith({ key: 'name', dir: 'asc' })
  })

  it('flips to descending when the column is already sorted ascending', async () => {
    const onSortChange = vi.fn()
    renderWithClient(<DataTable {...base} rows={[]} sort={{ key: 'name', dir: 'asc' }} onSortChange={onSortChange} />)
    await userEvent.click(screen.getByRole('button', { name: /name/i }))
    expect(onSortChange).toHaveBeenLastCalledWith({ key: 'name', dir: 'desc' })
    expect(screen.getByRole('columnheader', { name: /name/i })).toHaveAttribute('aria-sort', 'ascending')
  })

  it('pages with the buttons and stops at the ends', async () => {
    const onPageChange = vi.fn()
    renderWithClient(<DataTable {...base} rows={[]} page={3} onPageChange={onPageChange} />)
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Previous page' }))
    expect(onPageChange).toHaveBeenCalledWith(2)
  })

  it('shows skeleton rows while loading and the empty state when there is nothing', () => {
    const { unmount } = renderWithClient(<DataTable {...base} rows={undefined} isPending total={0} page={1} />)
    expect(screen.queryByText('Nothing here')).not.toBeInTheDocument()
    unmount()
    renderWithClient(<DataTable {...base} rows={[]} total={0} page={1} />)
    expect(screen.getByText('Nothing here')).toBeInTheDocument()
    expect(screen.getByText('No results')).toBeInTheDocument()
  })

  it('shows the error with a Retry instead of the table', async () => {
    const onRetry = vi.fn()
    renderWithClient(<DataTable {...base} rows={undefined} error={new Error('The server is down')} onRetry={onRetry} />)
    expect(screen.getByRole('alert')).toHaveTextContent('The server is down')
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalled()
  })
})
