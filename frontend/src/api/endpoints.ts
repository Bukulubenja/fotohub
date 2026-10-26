import { apiRequest } from './client'
import type {
  EventInfo,
  Gallery,
  Me,
  Page,
  Payment,
  Photo,
  PhotoStatus,
  Plan,
  StartedPayment,
  StartedUpload,
  Subscription,
  StorageUsage,
  UploadConfig,
  UploadedPart,
} from './types'

export const auth = {
  me: () => apiRequest<Me>('GET', '/me/'),
  login: (email: string, password: string) =>
    apiRequest<Me>('POST', '/auth/login/', { email, password }),
  logout: () => apiRequest<void>('POST', '/auth/logout/'),
}

export const galleries = {
  // First page is enough for the picker until galleries get their own search.
  list: () => apiRequest<Page<Gallery>>('GET', '/galleries/'),
}

export interface NewEvent {
  title: string
  event_date: string
  location_label: string
  description: string
}

export interface NewGallery {
  event: string
  title: string
  password?: string
  expires_at?: string | null
}

/** Creating and publishing galleries; swapped for a fake in tests. */
export interface GalleryAdminApi {
  createEvent(input: NewEvent): Promise<EventInfo>
  createGallery(input: NewGallery): Promise<Gallery>
  publish(id: string): Promise<Gallery>
  unpublish(id: string): Promise<Gallery>
}

export const galleryAdmin: GalleryAdminApi = {
  createEvent: (input) => apiRequest('POST', '/events/', input),
  createGallery: (input) => apiRequest('POST', '/galleries/', input),
  publish: (id) => apiRequest('POST', `/galleries/${id}/publish/`),
  unpublish: (id) => apiRequest('POST', `/galleries/${id}/unpublish/`),
}

export const photos = {
  count: async (gallery: string, status: PhotoStatus) => {
    const params = new URLSearchParams({ gallery, status })
    return (await apiRequest<Page<Photo>>('GET', `/photos/?${params}`)).count
  },
}

export const storage = {
  usage: () => apiRequest<StorageUsage>('GET', '/storage/usage/'),
}

export interface StartUploadInput {
  gallery: string
  filename: string
  contentType: string
  size: number
}

/** The upload endpoints the queue depends on; swapped for fakes in tests. */
export interface UploadApi {
  start(input: StartUploadInput): Promise<StartedUpload>
  resume(photoId: string): Promise<StartedUpload>
  signParts(photoId: string, partNumbers: number[]): Promise<Record<string, string>>
  listParts(photoId: string): Promise<UploadedPart[]>
  complete(photoId: string): Promise<Photo>
  abort(photoId: string): Promise<void>
}

export const uploadApi: UploadApi = {
  start: ({ gallery, filename, contentType, size }) =>
    apiRequest('POST', '/uploads/', { gallery, filename, content_type: contentType, size }),
  resume: (photoId) => apiRequest('GET', `/uploads/${photoId}/`),
  signParts: async (photoId, partNumbers) =>
    (
      await apiRequest<{ urls: Record<string, string> }>('POST', `/uploads/${photoId}/sign-parts/`, {
        part_numbers: partNumbers,
      })
    ).urls,
  listParts: async (photoId) =>
    (await apiRequest<{ parts: UploadedPart[] }>('GET', `/uploads/${photoId}/parts/`)).parts,
  complete: (photoId) => apiRequest('POST', `/uploads/${photoId}/complete/`),
  abort: (photoId) => apiRequest('DELETE', `/uploads/${photoId}/`),
}

export const uploadConfig = () => apiRequest<UploadConfig>('GET', '/uploads/config/')

/** Photographer subscription; swapped for a fake in tests. */
export interface BillingApi {
  plans(): Promise<Plan[]>
  subscription(): Promise<Subscription>
  startPayment(plan: string, periods: number, phone: string): Promise<StartedPayment>
  payment(id: string): Promise<Payment>
  /** Newest first: resumes a payment the photographer finished on Flutterwave's page. */
  payments(): Promise<Payment[]>
}

export const billing: BillingApi = {
  plans: () => apiRequest('GET', '/billing/plans/'),
  subscription: () => apiRequest('GET', '/billing/subscription/'),
  startPayment: (plan, periods, phone) =>
    apiRequest('POST', '/billing/payments/', { plan, periods, phone }),
  payment: (id) => apiRequest('GET', `/billing/payments/${id}/`),
  payments: async () => (await apiRequest<Page<Payment>>('GET', '/billing/payments/')).results,
}
