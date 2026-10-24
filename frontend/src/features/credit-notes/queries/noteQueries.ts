import { keepPreviousData, queryOptions } from '@tanstack/react-query'

import { noteService } from '../services/noteService'
import type { NoteQuery } from '../types/note'

export const noteKeys = {
  all: ['credit-notes'] as const,
  list: (query: NoteQuery) => [...noteKeys.all, 'list', query] as const,
  detail: (noteId: string) => [...noteKeys.all, 'detail', noteId] as const,
}

/** One page of the credit note table; the old page stays on screen while the next one loads. */
export const noteListQuery = (query: NoteQuery) =>
  queryOptions({
    queryKey: noteKeys.list(query),
    queryFn: () => noteService.search(query),
    placeholderData: keepPreviousData,
  })

export const noteQuery = (noteId: string) =>
  queryOptions({
    queryKey: noteKeys.detail(noteId),
    queryFn: () => noteService.get(noteId),
  })
