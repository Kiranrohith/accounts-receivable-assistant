import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { formatDate } from '@/utils/format'

import { useAddAlias } from '../queries/customerQueries'
import type { CustomerDetail } from '../types/customer'

interface AliasesSectionProps {
  customer: CustomerDetail
  /** Whether this person may confirm aliases (customers.alias.confirm). */
  canAdd: boolean
}

/** Other names and e-mail addresses a person confirmed for this customer, so later payments and mail from them are recognised. */
export function AliasesSection({ customer, canAdd }: AliasesSectionProps) {
  const add = useAddAlias(customer.id)
  const [type, setType] = useState<'name' | 'email'>('name')
  const [value, setValue] = useState('')

  return (
    <section aria-label="Aliases" className="space-y-2">
      <h3 className="text-sm font-semibold">Aliases</h3>
      {customer.aliases.length === 0 ? (
        <p className="text-sm text-muted-foreground">None confirmed yet.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {customer.aliases.map((a) => (
            <li key={a.alias_id}>
              <span className="font-medium">{a.alias_value}</span>{' '}
              <span className="text-muted-foreground">
                ({a.alias_type === 'email' ? 'e-mail' : 'name'}
                {a.confirmed_by ? `, confirmed by ${a.confirmed_by}${a.created_at ? ` on ${formatDate(a.created_at)}` : ''}` : a.source === 'seed' ? ', from the starting data' : ''})
              </span>
            </li>
          ))}
        </ul>
      )}
      {canAdd && (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (value.trim() === '') return
            add.mutate(
              { aliasType: type, aliasValue: value.trim() },
              {
                onSuccess: () => {
                  toast.success(`${value.trim()} was added as an alias.`)
                  setValue('')
                },
              },
            )
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="alias-type">Kind</Label>
            <select
              id="alias-type"
              className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm"
              value={type}
              onChange={(e) => setType(e.target.value as 'name' | 'email')}
            >
              <option value="name">Name</option>
              <option value="email">E-mail address</option>
            </select>
          </div>
          <div className="min-w-48 flex-1 space-y-1">
            <Label htmlFor="alias-value">Alias</Label>
            <Input id="alias-value" value={value} onChange={(e) => setValue(e.target.value)} />
          </div>
          <Button type="submit" variant="outline" disabled={add.isPending || value.trim() === ''}>
            Add alias
          </Button>
        </form>
      )}
    </section>
  )
}
