import { TAX_ID_TYPES, type NewCustomer, type TaxIdType } from '../types/customer'

// The same shapes the server checks, on the normalized id (upper case, no spaces, dots, hyphens or slashes). OTHER has none.
const SHAPES: Partial<Record<TaxIdType, RegExp>> = {
  GSTIN: /^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/,
  VAT: /^[A-Z]{2}[A-Z0-9]{2,13}$/,
  EIN: /^\d{9}$/,
  TRN: /^\d{15}$/,
}
const SHAPE_HELP: Record<TaxIdType, string> = {
  GSTIN: 'A GSTIN is 15 characters, like 29ABCDE1234F1Z5.',
  VAT: 'A VAT number is two letters and then 2 to 13 letters or digits, like GB 284 7731 09.',
  EIN: 'An EIN is 9 digits, like 94-3456789.',
  TRN: 'A TRN is 15 digits, like 100123456700003.',
  OTHER: '',
}
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export const TAX_ID_TYPE_LABEL: Record<TaxIdType, string> = { GSTIN: 'GSTIN', VAT: 'VAT number', TRN: 'TRN', EIN: 'EIN', OTHER: 'Other' }

/** The same rule the server stores by: upper case, without spaces, dots, hyphens or slashes. */
export const normalizeTaxId = (value: string): string => value.replace(/[\s.\-/]+/g, '').toUpperCase()

export const EMPTY_CUSTOMER: NewCustomer = {
  customer_name: '', contact: '', email: '', tax_id: '', tax_id_type: 'GSTIN', deducts_tds: false, billing_currency: 'INR', country: 'India',
}

export type CustomerErrors = Partial<Record<keyof NewCustomer, string>>

/** What is wrong with the form, in words a person can act on. Empty when it can be sent. */
export function validateNewCustomer(values: NewCustomer): CustomerErrors {
  const errors: CustomerErrors = {}
  if (values.customer_name.trim() === '') errors.customer_name = 'Enter the customer name.'
  if (values.contact.trim() === '') errors.contact = 'Enter a contact person.'
  if (!EMAIL.test(values.email.trim())) errors.email = 'Enter a valid e-mail address.'
  const taxId = normalizeTaxId(values.tax_id)
  const shape = SHAPES[values.tax_id_type]
  if (taxId === '') errors.tax_id = 'Enter the tax ID.'
  else if (shape && !shape.test(taxId)) errors.tax_id = SHAPE_HELP[values.tax_id_type]
  if (!(TAX_ID_TYPES as readonly string[]).includes(values.tax_id_type)) errors.tax_id_type = 'Choose what kind of tax ID it is.'
  if (!/^[A-Za-z]{3}$/.test(values.billing_currency.trim())) errors.billing_currency = 'Use a 3-letter currency code, like INR.'
  if (values.country.trim() === '') errors.country = 'Enter the country.'
  return errors
}

export type EditableFields = Pick<NewCustomer, 'customer_name' | 'contact' | 'email' | 'tax_id' | 'tax_id_type' | 'billing_currency' | 'country'>

/** Only what the person changed, cleaned the way a new customer's fields are. */
export function diffCustomerEdits(current: EditableFields, form: EditableFields): Partial<EditableFields> {
  const clean = toPayload({ ...EMPTY_CUSTOMER, ...form })
  const edits: Partial<EditableFields> = {}
  if (clean.customer_name !== current.customer_name.trim()) edits.customer_name = clean.customer_name
  if (clean.contact !== current.contact.trim()) edits.contact = clean.contact
  if (clean.email.toLowerCase() !== current.email.trim().toLowerCase()) edits.email = clean.email
  // the id and its type are checked together by the server, so a change to either sends both
  if (normalizeTaxId(clean.tax_id) !== normalizeTaxId(current.tax_id) || clean.tax_id_type !== current.tax_id_type) {
    edits.tax_id = clean.tax_id
    edits.tax_id_type = clean.tax_id_type
  }
  if (clean.billing_currency !== current.billing_currency.trim().toUpperCase()) edits.billing_currency = clean.billing_currency
  if (clean.country !== current.country.trim()) edits.country = clean.country
  return edits
}

/** The form as the API takes it: trimmed (the server stores the tax ID normalized), currency upper-cased. */
export function toPayload(values: NewCustomer): NewCustomer {
  return {
    customer_name: values.customer_name.trim(),
    contact: values.contact.trim(),
    email: values.email.trim(),
    tax_id: values.tax_id.trim(),
    tax_id_type: values.tax_id_type,
    deducts_tds: values.deducts_tds,
    billing_currency: values.billing_currency.trim().toUpperCase(),
    country: values.country.trim(),
  }
}
