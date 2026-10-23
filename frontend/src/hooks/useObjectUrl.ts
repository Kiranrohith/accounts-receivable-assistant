import { useEffect, useState } from 'react'

/**
 * A blob: URL for a fetched file, released when the file changes or the component goes away. It is made in an effect (not
 * during render) because React may run effects twice in development: a URL made during render would be revoked by the
 * first cleanup while still on screen.
 */
export function useObjectUrl(blob: Blob | undefined): string | null {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    if (!blob) return
    const objectUrl = URL.createObjectURL(blob)
    // eslint-disable-next-line react-hooks/set-state-in-effect -- syncing with an external resource (the browser's blob store)
    setUrl(objectUrl)
    return () => {
      URL.revokeObjectURL(objectUrl)
      setUrl(null)
    }
  }, [blob])
  return url
}
