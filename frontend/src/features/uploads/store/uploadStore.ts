import { Store, useStore } from '@tanstack/react-store'

import type { LocalUpload } from '../types/upload'

/**
 * The files picked in this session: what the browser knows before the server lists them (sending, refused, duplicate).
 * It is client-only state; the list of uploads the server holds stays in the query cache.
 */
export const uploadStore = new Store<{ items: LocalUpload[] }>({ items: [] })

export const addLocalUploads = (items: LocalUpload[]) => uploadStore.setState((s) => ({ items: [...items, ...s.items] }))

export const updateLocalUpload = (key: string, patch: Partial<LocalUpload>) =>
  uploadStore.setState((s) => ({ items: s.items.map((item) => (item.key === key ? { ...item, ...patch } : item)) }))

export const dismissLocalUpload = (key: string) => uploadStore.setState((s) => ({ items: s.items.filter((item) => item.key !== key) }))

export const clearLocalUploads = () => uploadStore.setState(() => ({ items: [] }))

export const useLocalUploads = () => useStore(uploadStore, (s) => s.items)
