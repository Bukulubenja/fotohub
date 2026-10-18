// Mirrors the DRF serializers in accounts/, galleries/ and assets/.

export interface Me {
  email: string
  display_name: string
  contact_phone: string
  brand_color: string
}

export type GalleryStatus = 'draft' | 'processing' | 'published' | 'archived' | 'deletion_pending'

export interface Gallery {
  id: string
  event: string
  title: string
  status: GalleryStatus
}

export type PhotoStatus = 'uploading' | 'processing' | 'ready' | 'failed'

export interface Photo {
  id: string
  gallery: string
  original_filename: string
  size_bytes: number
  status: PhotoStatus
  failure_reason: string
  thumbnail_url: string | null
}

export type UploadTarget =
  | {
      type: 'single'
      method: 'PUT'
      url: string
      headers: Record<string, string>
      expires_at: string
    }
  | { type: 'multipart'; part_size: number; part_count: number; expires_at: string }

export interface StartedUpload {
  photo: Photo
  upload: UploadTarget
}

export interface UploadedPart {
  PartNumber: number
  Size: number
}

export interface StorageUsage {
  used_bytes: number
  quota_bytes: number
  percent: number
  warning_level: number | null
}

export interface UploadConfig {
  allowed_content_types: string[]
  max_file_bytes: number
  max_parts_per_sign_request: number
}

export interface Page<T> {
  count: number
  next: string | null
  results: T[]
}
