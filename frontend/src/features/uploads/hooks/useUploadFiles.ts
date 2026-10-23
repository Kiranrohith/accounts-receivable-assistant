import { useQueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'

import { ApiError } from '@/lib/axios'

import { uploadKeys } from '../queries/uploadQueries'
import { uploadService } from '../services/uploadService'
import { addLocalUploads, updateLocalUpload } from '../store/uploadStore'
import type { LocalUpload } from '../types/upload'

// Mirrors the server's upload limit so a huge file is not sent just to be refused. The server stays the authority.
export const MAX_UPLOAD_MB = 20
const MAX_PARALLEL = 3

let running = 0
const waiting: Array<() => void> = []
let counter = 0

/** At most three files travel at once; the rest wait their turn. */
function schedule(job: () => Promise<void>) {
  const start = () => {
    running += 1
    void job().finally(() => {
      running -= 1
      waiting.shift()?.()
    })
  }
  if (running < MAX_PARALLEL) start()
  else waiting.push(start)
}

/** The server's duplicate message carries an internal id; the person needs only the fact. */
const friendlyDuplicate = 'This file was already uploaded.'

/**
 * Send the chosen files, each as its own upload. Every one gets a row at once (Uploading, with its progress); when the server
 * accepts it, the list is refreshed and the server's own row takes over. A refused file stays as a row that says why.
 */
export function useUploadFiles() {
  const qc = useQueryClient()

  return useCallback(
    (files: File[]) => {
      const locals: LocalUpload[] = files.map((file) => ({
        key: `local-${Date.now()}-${(counter += 1)}`,
        fileName: file.name,
        size: file.size,
        state: 'uploading',
        progress: 0,
      }))
      addLocalUploads(locals)

      files.forEach((file, index) => {
        const { key } = locals[index]
        if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
          updateLocalUpload(key, { state: 'rejected', message: `File is larger than ${MAX_UPLOAD_MB} MB` })
          return
        }
        schedule(async () => {
          try {
            const { source_document_id } = await uploadService.upload(file, (progress) => updateLocalUpload(key, { progress }))
            updateLocalUpload(key, { state: 'uploaded', progress: 100, sourceDocumentId: source_document_id })
            await qc.invalidateQueries({ queryKey: uploadKeys.all })
          } catch (error) {
            const status = error instanceof ApiError ? error.status : 0
            const message = error instanceof Error ? error.message : 'The upload failed.'
            if (status === 409) updateLocalUpload(key, { state: 'duplicate', message: friendlyDuplicate })
            else if (status === 422) updateLocalUpload(key, { state: 'rejected', message })
            else updateLocalUpload(key, { state: 'failed', message })
          }
        })
      })
    },
    [qc],
  )
}
