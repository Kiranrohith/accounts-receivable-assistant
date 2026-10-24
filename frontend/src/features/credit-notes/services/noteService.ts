import { api } from '@/lib/axios'
import type { Page } from '@/types/api'

import type { NoteDetail, NoteListItem, NoteQuery } from '../types/note'
import { toSearchParams } from '../utils/view'

export const noteService = {
  /** One page of the credit note table: filtered, searched and sorted by the server, with the total that matches. */
  async search(query: NoteQuery): Promise<Page<NoteListItem>> {
    const { data } = await api.get<Page<NoteListItem>>('/notes/search', { params: toSearchParams(query) })
    return data
  },

  async get(noteId: string): Promise<NoteDetail> {
    const { data } = await api.get<NoteDetail>(`/notes/${noteId}`)
    return data
  },
}
