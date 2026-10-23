import { Store, useStore } from '@tanstack/react-store'

/** App-wide client-only UI state. Server data never goes here: the query cache owns it. */
export const uiStore = new Store({ sidebarCollapsed: false })

export const toggleSidebar = () => uiStore.setState((s) => ({ ...s, sidebarCollapsed: !s.sidebarCollapsed }))
export const useSidebarCollapsed = () => useStore(uiStore, (s) => s.sidebarCollapsed)
