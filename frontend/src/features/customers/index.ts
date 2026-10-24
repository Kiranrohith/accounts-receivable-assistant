/** Public API of the customers feature. */
export { CustomerFilter } from './components/CustomerFilter'
export { CustomersPage } from './components/CustomersPage'
export { customerKeys, customerQuery, customersQuery, useCreateCustomer } from './queries/customerQueries'
export { TAX_ID_TYPES } from './types/customer'
export type { Customer, CustomerDetail, NewCustomer, TaxIdType } from './types/customer'
export { TAX_ID_TYPE_LABEL } from './utils/newCustomer'
