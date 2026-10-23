import type { NewCustomer } from '../types/customer'

// The same shape the server checks: 15 characters, state code, PAN, entity, Z, check digit.
const GSTIN = /^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export const EMPTY_CUSTOMER: NewCustomer = {
  customer_name: '', contact: '', email: '', gstin: '', deducts_tds: false, billing_currency: 'INR', country: 'India',
}

export type CustomerErrors = Partial<Record<keyof NewCustomer, string>>

/** What is wrong with the form, in words a person can act on. Empty when it can be sent. */
export function validateNewCustomer(values: NewCustomer): CustomerErrors {
  const errors: CustomerErrors = {}
  if (values.customer_name.trim() === '') errors.customer_name = 'Enter the customer name.'
  if (values.contact.trim() === '') errors.contact = 'Enter a contact person.'
  if (!EMAIL.test(values.email.trim())) errors.email = 'Enter a valid e-mail address.'
  if (!GSTIN.test(values.gstin.trim().toUpperCase())) errors.gstin = 'A GSTIN is 15 characters, like 29ABCDE1234F1Z5.'
  if (!/^[A-Za-z]{3}$/.test(values.billing_currency.trim())) errors.billing_currency = 'Use a 3-letter currency code, like INR.'
  if (values.country.trim() === '') errors.country = 'Enter the country.'
  return errors
}

export type EditableFields = Pick<NewCustomer, 'customer_name' | 'contact' | 'email' | 'gstin' | 'billing_currency' | 'country'>

/** Only what the person changed, cleaned the way a new customer's fields are. */
export function diffCustomerEdits(current: EditableFields, form: EditableFields): Partial<EditableFields> {
  const clean = toPayload({ ...EMPTY_CUSTOMER, ...form })
  const edits: Partial<EditableFields> = {}
  if (clean.customer_name !== current.customer_name.trim()) edits.customer_name = clean.customer_name
  if (clean.contact !== current.contact.trim()) edits.contact = clean.contact
  if (clean.email.toLowerCase() !== current.email.trim().toLowerCase()) edits.email = clean.email
  if (clean.gstin !== current.gstin.trim().toUpperCase()) edits.gstin = clean.gstin
  if (clean.billing_currency !== current.billing_currency.trim().toUpperCase()) edits.billing_currency = clean.billing_currency
  if (clean.country !== current.country.trim()) edits.country = clean.country
  return edits
}

/** The form as the API takes it: trimmed, GSTIN and currency upper-cased. */
export function toPayload(values: NewCustomer): NewCustomer {
  return {
    customer_name: values.customer_name.trim(),
    contact: values.contact.trim(),
    email: values.email.trim(),
    gstin: values.gstin.trim().toUpperCase(),
    deducts_tds: values.deducts_tds,
    billing_currency: values.billing_currency.trim().toUpperCase(),
    country: values.country.trim(),
  }
}
