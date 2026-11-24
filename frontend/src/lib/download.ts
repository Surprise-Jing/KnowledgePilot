/** Saves a blob through a temporary link. Some browsers cancel the download when the object
 * URL is revoked in the same task as the click, so the URL is released a little later. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1_000)
}
