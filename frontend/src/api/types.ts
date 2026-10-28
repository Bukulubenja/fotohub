// Mirrors the DRF serializers in accounts/, galleries/ and assets/.

export interface Me {
  email: string
  display_name: string
  contact_phone: string
  brand_color: string
}

export interface EventInfo {
  id: string
  title: string
  event_date: string
  description: string
  location_label: string
  /** The collection (e.g. a school) whose passkey opens this event's galleries. */
  collection: string | null
  /** In a collection, but its galleries keep their own password. */
  separate_access: boolean
}

export type CollectionStatus = 'active' | 'archived'

/** galleries/serializers.py CollectionSerializer. The passkey is meant to be shared. */
export interface Collection {
  id: string
  name: string
  passkey: string
  status: CollectionStatus
  event_count: number
  /** The guest home page, and a WhatsApp-ready message naming it and the passkey. */
  share_url: string
  share_message: string
}

export type GalleryStatus = 'draft' | 'processing' | 'published' | 'archived' | 'deletion_pending'

export interface Gallery {
  id: string
  event: string
  title: string
  status: GalleryStatus
  has_password: boolean
  expires_at: string | null
  /** Customer link, and a WhatsApp-ready message containing it (never the password). */
  share_url: string
  share_message: string
}

/** galleries/stats.py summary(). Gross is per currency, never converted or netted. */
export interface GalleryStats {
  views: number
  visitors: number
  favourites: number
  orders: number
  photos_sold: number
  gross: { currency: string; amount_minor: number; formatted: string }[]
  conversion_percent: number | null
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

// --- Billing (billing/serializers.py) ---------------------------------------------------

export interface Plan {
  code: string
  name: string
  price_ugx: number
  period_days: number
  storage_quota_bytes: number
}

export type SubscriptionState = 'active' | 'due' | 'grace' | 'lapsed'

export interface Subscription {
  plan: Plan
  paid_until: string
  state: SubscriptionState
  can_upload: boolean
  galleries_live: boolean
  days_left: number
}

export type PaymentStatus = 'pending' | 'succeeded' | 'failed'

export interface Payment {
  id: string
  plan: string
  periods: number
  amount_ugx: number
  status: PaymentStatus
  failure_reason: string
  created_at: string
}

export interface StartedPayment {
  payment: Payment
  next: { action: 'prompt' | 'redirect'; message: string; redirect_url: string }
}
