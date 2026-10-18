import type { UploadConfig } from '../api/types'
import { formatBytes } from '../format'
import { contentTypeFor } from './fileType'

export interface Rejected {
  name: string
  reason: string
}

/** Catch wrong types and oversized files before any network call. */
export function screenFiles(files: File[], config: UploadConfig) {
  const accepted: { file: File; contentType: string }[] = []
  const rejected: Rejected[] = []
  for (const file of files) {
    const contentType = contentTypeFor(file, config.allowed_content_types)
    if (!contentType) rejected.push({ name: file.name, reason: 'Not a JPEG, PNG, WebP or HEIC photo' })
    else if (file.size > config.max_file_bytes)
      rejected.push({ name: file.name, reason: `Larger than ${formatBytes(config.max_file_bytes)}` })
    else if (file.size === 0) rejected.push({ name: file.name, reason: 'Empty file' })
    else accepted.push({ file, contentType })
  }
  return { accepted, rejected }
}
