import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Plus } from 'lucide-react'
import { useEffect, useState } from 'react'

import { DataTable, type Column } from '@/components/common/DataTable'
import { PageHeader } from '@/components/common/PageHeader'
import { EmptyState } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { PERMISSIONS } from '@/config/constants'
import { hasPermission, useCurrentUser } from '@/features/auth'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { formatCurrency } from '@/utils/format'

import { customersQuery } from '../queries/customerQueries'
import type { Customer } from '../types/customer'
import { viewCustomers } from '../utils/view'
import { AddCustomerDialog } from './AddCustomerDialog'
import { CustomerModal } from './CustomerModal'

interface CustomersPageProps {
  q: string | undefined
  sort: string | undefined
  dir: 'asc' | 'desc' | undefined
  page: number
  size: number
  /** The customer whose details are open, from the URL. */
  customer: string | undefined
}

const COLUMNS: Column<Customer>[] = [
  { key: 'name', header: 'Customer', sortKey: 'name', cell: (c) => <span className="font-medium">{c.customer_name}</span> },
  { key: 'tax_id', header: 'Tax ID', sortKey: 'tax_id', cell: (c) => (c.tax_id ? <span>{c.tax_id}{c.tax_id_type && <span className="ml-1 text-xs text-muted-foreground">{c.tax_id_type}</span>}</span> : '—') },
  { key: 'currency', header: 'Currency', sortKey: 'currency', cell: (c) => c.billing_currency ?? '—' },
  { key: 'tds', header: 'TDS', cell: (c) => (c.deducts_tds ? <StatusChip status="TDS" label="Deducts TDS" /> : <span className="text-muted-foreground">—</span>) },
  { key: 'open', header: 'Open', sortKey: 'open', align: 'right', cell: (c) => (
    <span>
      {c.open_invoices}
      {c.overdue_invoices > 0 && <span className="block text-xs text-red-700">{c.overdue_invoices} overdue</span>}
    </span>
  ) },
  { key: 'outstanding', header: 'Outstanding', sortKey: 'outstanding', align: 'right', cell: (c) => formatCurrency(c.outstanding, 'INR') },
  { key: 'unapplied', header: 'Unapplied cash', align: 'right', cell: (c) => (
    Number(c.unapplied_cash) > 0 ? <StatusChip status="Unapplied" label={formatCurrency(c.unapplied_cash, 'INR')} /> : <span className="text-muted-foreground">—</span>
  ) },
  { key: 'contact', header: 'Contact', cell: (c) => (
    <span>
      {c.contact ?? '—'}
      {c.email && <span className="block text-xs text-muted-foreground">{c.email}</span>}
    </span>
  ) },
]

export function CustomersPage({ q, sort, dir, page, size, customer }: CustomersPageProps) {
  const navigate = useNavigate({ from: '/customers' })
  const customers = useQuery(customersQuery())
  const canAdd = hasPermission(useCurrentUser(), PERMISSIONS.customersManage)
  const [adding, setAdding] = useState(false)

  const [text, setText] = useState(q ?? '')
  const debounced = useDebouncedValue(text)
  useEffect(() => {
    if (debounced !== (q ?? '')) void navigate({ search: (prev) => ({ ...prev, q: debounced || undefined, page: 1 }), replace: true })
  }, [debounced]) // eslint-disable-line react-hooks/exhaustive-deps

  const view = customers.data ? viewCustomers(customers.data, { q, sort, dir, page, size }) : undefined

  return (
    <>
      <PageHeader title="Customers" description="Everyone we invoice.">
        {canAdd && (
          <Button onClick={() => setAdding(true)}>
            <Plus /> Add customer
          </Button>
        )}
      </PageHeader>

      <DataTable
        columns={COLUMNS}
        rows={view?.rows}
        total={view?.total ?? 0}
        rowKey={(c) => c.id}
        page={page}
        pageSize={size}
        onPageChange={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        onPageSizeChange={(next) => navigate({ search: (prev) => ({ ...prev, size: next, page: 1 }) })}
        sort={{ key: sort ?? 'name', dir: dir ?? 'asc' }}
        onSortChange={(next) => navigate({ search: (prev) => ({ ...prev, sort: next.key, dir: next.dir, page: 1 }) })}
        isPending={customers.isPending}
        error={customers.error}
        onRetry={() => customers.refetch()}
        onRowClick={(c) => navigate({ search: (prev) => ({ ...prev, customer: c.id }) })}
        selectedKey={customer}
        toolbar={<Input type="search" aria-label="Search customers" placeholder="Search by name, tax ID or e-mail" className="max-w-xs" value={text} onChange={(e) => setText(e.target.value)} />}
        empty={
          q ? (
            <EmptyState message="No customer matches that search." />
          ) : (
            <EmptyState message="There are no customers yet." action={canAdd ? <Button onClick={() => setAdding(true)}>Add customer</Button> : undefined} />
          )
        }
      />

      <CustomerModal customerId={customer} onClose={() => void navigate({ search: (prev) => ({ ...prev, customer: undefined }) })} />
      {canAdd && <AddCustomerDialog open={adding} onOpenChange={setAdding} />}
    </>
  )
}
