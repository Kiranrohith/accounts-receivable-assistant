import { X } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'

import { entityLabel } from '../utils/view'

const ALL = 'all'

/** A drop-down with an "everything" choice first; choosing it clears the filter. */
export function ChoiceFilter({ label, allLabel, value, options, show, onChange }: {
  label: string
  allLabel: string
  value: string | undefined
  options: readonly string[]
  show: (option: string) => string
  onChange: (next: string | undefined) => void
}) {
  return (
    <Select value={value ?? ALL} onValueChange={(next) => onChange(next === ALL ? undefined : next)}>
      <SelectTrigger size="sm" className="w-44" aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>{allLabel}</SelectItem>
        {options.map((option) => (
          <SelectItem key={option} value={option}>
            {show(option)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/** The kind-of-record filter both logs share. */
export function KindFilter({ value, options, onChange }: { value: string | undefined; options: readonly string[]; onChange: (next: string | undefined) => void }) {
  return <ChoiceFilter label="Kind of record" allLabel="All kinds" value={value} options={options} show={entityLabel} onChange={onChange} />
}

/** The two days that bound a log, both included, in India time. */
export function DayWindow({ from, to, onChange }: { from: string | undefined; to: string | undefined; onChange: (next: { from?: string; to?: string }) => void }) {
  return (
    <>
      <div className="flex items-center gap-2">
        <Label htmlFor="log-from" className="text-xs text-muted-foreground">
          From
        </Label>
        <Input id="log-from" type="date" className="h-8 w-40" value={from ?? ''} max={to || undefined} onChange={(e) => onChange({ from: e.target.value || undefined, to })} />
      </div>
      <div className="flex items-center gap-2">
        <Label htmlFor="log-to" className="text-xs text-muted-foreground">
          to
        </Label>
        <Input id="log-to" type="date" className="h-8 w-40" value={to ?? ''} min={from || undefined} onChange={(e) => onChange({ from, to: e.target.value || undefined })} />
      </div>
    </>
  )
}

/** The search box: the text goes to the URL once typing pauses. */
export function LogSearch({ label, placeholder, value, onChange }: { label: string; placeholder: string; value: string | undefined; onChange: (next: string | undefined) => void }) {
  const [text, setText] = useState(value ?? '')
  const debounced = useDebouncedValue(text)
  useEffect(() => {
    if (debounced !== (value ?? '')) onChange(debounced || undefined)
  }, [debounced]) // eslint-disable-line react-hooks/exhaustive-deps
  return <Input type="search" aria-label={label} placeholder={placeholder} className="max-w-xs" value={text} onChange={(e) => setText(e.target.value)} />
}

/** "Showing only this record": a removable chip, so a timeline opened from a record page can be widened again. */
export function ScopeChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md border bg-accent px-2 py-1 text-xs text-accent-foreground">
      {label}
      <Button type="button" variant="ghost" size="icon-sm" className="size-5" aria-label={`Stop showing only ${label}`} onClick={onClear}>
        <X className="size-3" />
      </Button>
    </span>
  )
}
