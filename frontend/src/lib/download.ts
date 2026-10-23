/** The file name the server suggested in a `Content-Disposition` header, or `fallback` when it did not. */
export function fileNameFrom(contentDisposition: string | undefined, fallback: string): string {
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(contentDisposition ?? '')
  return match ? decodeURIComponent(match[1]) : fallback
}

/** Hands a file the browser fetched to the person: a click on a temporary link, then the temporary address is released. */
export function saveBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  document.body.appendChild(link)
  link.click()
  link.remove()
  // not at once: some browsers start the download after the click returns
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
