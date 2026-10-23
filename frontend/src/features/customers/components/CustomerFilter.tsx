import { useQuery } from '@tanstack/react-query'

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

import { customersQuery } from '../queries/customerQueries'

const ALL = 'all'

interface CustomerFilterProps {
  /** The customer chosen, or undefined for everyone. */
  value: string | undefined
  onChange: (customerId: string | undefined) => void
}

/** "All customers" or one: the filter a list puts in its toolbar. */
export function CustomerFilter({ value, onChange }: CustomerFilterProps) {
  const customers = useQuery(customersQuery())
  return (
    <Select value={value ?? ALL} onValueChange={(next) => onChange(next === ALL ? undefined : next)}>
      <SelectTrigger size="sm" className="w-56" aria-label="Customer">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>All customers</SelectItem>
        {customers.data?.map((c) => (
          <SelectItem key={c.id} value={c.id}>
            {c.customer_name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
