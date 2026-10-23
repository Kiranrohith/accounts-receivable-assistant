import { UploadCloud } from 'lucide-react'
import { useRef, useState, type DragEvent } from 'react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

// What the server reads. Only a hint for the file chooser: the server decides what it accepts.
const ACCEPT = '.pdf,.docx,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp'

interface DropZoneProps {
  onFiles: (files: File[]) => void
}

/** One entry for every kind of document: drop files here or choose them. The system works out what each one is. */
export function DropZone({ onFiles }: DropZoneProps) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)

  function drop(event: DragEvent) {
    event.preventDefault()
    setOver(false)
    const files = Array.from(event.dataTransfer.files)
    if (files.length > 0) onFiles(files)
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
      className={cn(
        'flex flex-col items-center gap-3 rounded-lg border-2 border-dashed bg-card px-6 py-10 text-center transition-colors',
        over ? 'border-primary bg-accent/50' : 'border-border',
      )}
    >
      <UploadCloud className="size-9 text-muted-foreground" aria-hidden />
      <div>
        <p className="font-medium">Drop files here</p>
        <p className="text-sm text-muted-foreground">Invoices and purchase orders — we'll detect which. PDF, Word or image, up to 20 MB each.</p>
      </div>
      <Button type="button" onClick={() => input.current?.click()}>
        Choose files
      </Button>
      <input
        ref={input}
        type="file"
        multiple
        accept={ACCEPT}
        aria-label="Choose files to upload"
        className="sr-only"
        tabIndex={-1}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? [])
          if (files.length > 0) onFiles(files)
          e.target.value = '' // choosing the same file again must still fire a change
        }}
      />
    </div>
  )
}
