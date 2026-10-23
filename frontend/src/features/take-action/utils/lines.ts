/** One editable line, as the text in its inputs. */
export interface LineForm {
  description: string
  quantity: string
  unit_price: string
  line_total: string
  tax_rate: string
}

export interface LineSource {
  description: string
  quantity: string | null
  unit_price: string | null
  line_total: string | null
  tax_rate?: string | null
}

export const emptyLine = (): LineForm => ({ description: '', quantity: '', unit_price: '', line_total: '', tax_rate: '' })

export const toLineForms = (lines: LineSource[]): LineForm[] =>
  lines.map((l) => ({
    description: l.description,
    quantity: l.quantity ?? '',
    unit_price: l.unit_price ?? '',
    line_total: l.line_total ?? '',
    tax_rate: l.tax_rate ?? '',
  }))

const blank = (line: LineForm) => Object.values(line).every((v) => v.trim() === '')
const decimal = (value: string): string | null => (value.trim() === '' ? null : value.trim())

/** The lines as the API takes them: rows nobody filled in are dropped, empty numbers are null. */
export function toLinePayload(lines: LineForm[]) {
  return lines
    .filter((l) => !blank(l))
    .map((l) => ({
      description: l.description.trim(),
      quantity: decimal(l.quantity),
      unit_price: decimal(l.unit_price),
      line_total: decimal(l.line_total),
      tax_rate: decimal(l.tax_rate),
    }))
}

const sameNumber = (a: string, b: string) => (a.trim() === '' || b.trim() === '' ? a.trim() === b.trim() : Number(a) === Number(b))

/** Whether the person changed anything in the lines (1 and 1.00 are the same quantity). */
export function linesChanged(original: LineForm[], now: LineForm[]): boolean {
  const kept = now.filter((l) => !blank(l))
  if (kept.length !== original.length) return true
  return kept.some((l, i) => {
    const o = original[i]
    return (
      l.description.trim() !== o.description.trim() ||
      !sameNumber(l.quantity, o.quantity) ||
      !sameNumber(l.unit_price, o.unit_price) ||
      !sameNumber(l.line_total, o.line_total) ||
      !sameNumber(l.tax_rate, o.tax_rate)
    )
  })
}
