import {
  Activity, AlertTriangle, BarChart3, BellRing, Building2, HandCoins, FileMinus, FileSpreadsheet, FileText, Inbox, LayoutDashboard, ListChecks, Mail, Scale, ScrollText,
  Settings, Upload, UserCog, Wallet, type LucideIcon,
} from 'lucide-react'

import type { AccessKey } from '@/features/auth'

export interface NavItem {
  to:
    | '/dashboard' | '/my-work' | '/take-action' | '/upload' | '/invoices' | '/purchase-orders' | '/payments' | '/credit-notes'
    | '/disputes' | '/reminders' | '/collection-tasks' | '/customers' | '/reports' | '/mail' | '/mail-live' | '/failed-items' | '/audit-log' | '/users' | '/settings'
  label: string
  icon: LucideIcon
  access: AccessKey
}

/** Sidebar order from the design (section 1). Items the role lacks are hidden, not disabled. */
export const NAV_ITEMS: NavItem[] = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard, access: 'dashboard' },
  { to: '/my-work', label: 'My Work', icon: Inbox, access: 'myWork' },
  { to: '/take-action', label: 'Take Action', icon: ListChecks, access: 'takeAction' },
  { to: '/upload', label: 'Upload Documents', icon: Upload, access: 'upload' },
  { to: '/invoices', label: 'Invoices', icon: FileText, access: 'invoices' },
  { to: '/purchase-orders', label: 'Purchase Orders', icon: FileSpreadsheet, access: 'purchaseOrders' },
  { to: '/payments', label: 'Payments', icon: Wallet, access: 'payments' },
  { to: '/credit-notes', label: 'Credit Notes', icon: FileMinus, access: 'creditNotes' },
  { to: '/disputes', label: 'Disputes', icon: Scale, access: 'disputes' },
  { to: '/reminders', label: 'Reminders', icon: BellRing, access: 'reminders' },
  { to: '/collection-tasks', label: 'Collection Tasks', icon: HandCoins, access: 'collectionTasks' },
  { to: '/customers', label: 'Customers', icon: Building2, access: 'customers' },
  { to: '/reports', label: 'Reports', icon: BarChart3, access: 'reports' },
  { to: '/mail', label: 'Mail Log', icon: Mail, access: 'mail' },
  { to: '/mail-live', label: 'Live Mail', icon: Activity, access: 'mail' },
  { to: '/failed-items', label: 'Failed Items', icon: AlertTriangle, access: 'failedItems' },
  { to: '/audit-log', label: 'Audit Log', icon: ScrollText, access: 'auditLog' },
  { to: '/users', label: 'Users', icon: UserCog, access: 'users' },
  { to: '/settings', label: 'Settings', icon: Settings, access: 'settings' },
]

/** The top bar's page title: the nav item whose path the current location sits under. */
export function titleFor(pathname: string): string {
  if (pathname.startsWith('/profile')) return 'Profile'
  if (pathname.startsWith('/forbidden')) return 'No access'
  return NAV_ITEMS.find((item) => pathname === item.to || pathname.startsWith(`${item.to}/`))?.label ?? 'Genworx AR'
}
