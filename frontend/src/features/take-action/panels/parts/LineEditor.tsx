import { Plus, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

import { emptyLine, type LineForm } from '../../utils/lines'

interface LineEditorProps {
  lines: LineForm[]
  onChange: (lines: LineForm[]) => void
  /** Invoice lines carry a tax rate; purchase-order lines do not. */
  withTaxRate?: boolean
}

/** Editable line items: description, quantity, unit price, total (and tax rate). Rows can be added and removed. */
export function LineEditor({ lines, onChange, withTaxRate = false }: LineEditorProps) {
  const set = (index: number, key: keyof LineForm, value: string) =>
    onChange(lines.map((line, i) => (i === index ? { ...line, [key]: value } : line)))

  const cell = (index: number, key: keyof LineForm, label: string, className = '') => (
    <Input
      aria-label={`Line ${index + 1} ${label}`}
      className={className}
      inputMode={key === 'description' ? undefined : 'decimal'}
      value={lines[index][key]}
      onChange={(e) => set(index, key, e.target.value)}
    />
  )

  return (
    <div className="space-y-2">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-muted-foreground">
            <th className="py-1 font-normal">Description</th>
            <th className="py-1 font-normal">Qty</th>
            <th className="py-1 font-normal">Unit price</th>
            {withTaxRate && <th className="py-1 font-normal">Tax %</th>}
            <th className="py-1 font-normal">Total</th>
            <th className="w-8" />
          </tr>
        </thead>
        <tbody>
          {lines.map((_, index) => (
            <tr key={index} className="align-top">
              <td className="py-1 pr-1">{cell(index, 'description', 'description')}</td>
              <td className="w-16 py-1 pr-1">{cell(index, 'quantity', 'quantity')}</td>
              <td className="w-24 py-1 pr-1">{cell(index, 'unit_price', 'unit price')}</td>
              {withTaxRate && <td className="w-16 py-1 pr-1">{cell(index, 'tax_rate', 'tax rate')}</td>}
              <td className="w-24 py-1 pr-1">{cell(index, 'line_total', 'total')}</td>
              <td className="py-1">
                <Button variant="ghost" size="icon-sm" aria-label={`Remove line ${index + 1}`} onClick={() => onChange(lines.filter((_, i) => i !== index))}>
                  <Trash2 />
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <Button variant="outline" size="sm" onClick={() => onChange([...lines, emptyLine()])}>
        <Plus /> Add a line
      </Button>
    </div>
  )
}
