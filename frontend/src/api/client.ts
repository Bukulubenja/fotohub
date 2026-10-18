// Thin fetch wrapper for the Django API: session cookie + CSRF header, DRF error parsing.

const API_ROOT = '/api/v1'
const CSRF_COOKIE = 'csrftoken'
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export function readCookie(name: string, cookies: string = document.cookie): string | null {
  const match = cookies.split('; ').find((part) => part.startsWith(`${name}=`))
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null
}

/** DRF sends {"detail": "..."} or {"field": ["msg", ...]}; reduce either to one sentence. */
export function errorMessage(body: unknown, status: number): string {
  if (body && typeof body === 'object') {
    const data = body as Record<string, unknown>
    if (typeof data.detail === 'string') return data.detail
    const first = Object.values(data)[0]
    if (Array.isArray(first) && typeof first[0] === 'string') return first[0]
  }
  if (status === 0) return 'No connection. Check your internet and try again.'
  if (status >= 500) return 'The server had a problem. Try again in a moment.'
  return `Request failed (${status}).`
}

export async function ensureCsrfCookie(): Promise<void> {
  if (!readCookie(CSRF_COOKIE)) await fetch(`${API_ROOT}/auth/csrf/`, { credentials: 'same-origin' })
}

export async function apiRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (!SAFE_METHODS.has(method)) {
    await ensureCsrfCookie()
    headers['X-CSRFToken'] = readCookie(CSRF_COOKIE) ?? ''
  }

  let response: Response
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      method,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, errorMessage(null, 0))
  }

  if (response.status === 204) return undefined as T
  const data: unknown = await response.json().catch(() => null)
  if (!response.ok) throw new ApiError(response.status, errorMessage(data, response.status))
  return data as T
}
