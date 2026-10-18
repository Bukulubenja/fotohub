import { ApiError, apiRequest, errorMessage, readCookie } from './client'
import { contentTypeFor } from '../upload/fileType'

function respond(status: number, body?: unknown) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  document.cookie = 'csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT'
})

test('reads a cookie by exact name', () => {
  expect(readCookie('csrftoken', 'xcsrftoken=no; csrftoken=abc%3D')).toBe('abc=')
  expect(readCookie('missing', 'a=1')).toBeNull()
})

test('sends the CSRF token on unsafe requests only', async () => {
  document.cookie = 'csrftoken=tok123'
  const fetchMock = vi.fn(async () => respond(200, { ok: true }))
  vi.stubGlobal('fetch', fetchMock)

  await apiRequest('POST', '/uploads/', { a: 1 })
  await apiRequest('GET', '/me/')

  const [postInit, getInit] = fetchMock.mock.calls.map((call) => (call as unknown[])[1] as RequestInit)
  expect((postInit.headers as Record<string, string>)['X-CSRFToken']).toBe('tok123')
  expect(postInit.body).toBe('{"a":1}')
  expect((getInit.headers as Record<string, string>)['X-CSRFToken']).toBeUndefined()
})

test('turns DRF errors into ApiError with a readable message', async () => {
  document.cookie = 'csrftoken=tok'
  vi.stubGlobal('fetch', vi.fn(async () => respond(409, { detail: 'Over your storage limit.' })))

  await expect(apiRequest('POST', '/uploads/', {})).rejects.toEqual(
    new ApiError(409, 'Over your storage limit.'),
  )
})

test('reports network failures as status 0 so the queue can retry them', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => {
    throw new TypeError('Failed to fetch')
  }))

  await expect(apiRequest('GET', '/me/')).rejects.toMatchObject({ status: 0 })
})

test('returns undefined for 204 responses', async () => {
  document.cookie = 'csrftoken=tok'
  vi.stubGlobal('fetch', vi.fn(async () => respond(204)))

  await expect(apiRequest('DELETE', '/uploads/x/')).resolves.toBeUndefined()
})

test.each([
  [{ detail: 'Nope.' }, 400, 'Nope.'],
  [{ size: ['Ensure this value is less than or equal to 100.'] }, 400, 'Ensure this value is less than or equal to 100.'],
  [null, 502, 'The server had a problem. Try again in a moment.'],
])('errorMessage(%j, %i)', (body, status, expected) => {
  expect(errorMessage(body, status)).toBe(expected)
})

describe('contentTypeFor', () => {
  const allowed = ['image/jpeg', 'image/png', 'image/heic']

  test('trusts an allowed browser type', () => {
    expect(contentTypeFor({ name: 'a.bin', type: 'image/png' }, allowed)).toBe('image/png')
  })

  test('falls back to the extension when the browser gives no type (HEIC on Windows)', () => {
    expect(contentTypeFor({ name: 'IMG_0001.HEIC', type: '' }, allowed)).toBe('image/heic')
  })

  test('rejects anything else', () => {
    expect(contentTypeFor({ name: 'notes.pdf', type: 'application/pdf' }, allowed)).toBeNull()
    expect(contentTypeFor({ name: 'shot.webp', type: '' }, allowed)).toBeNull()
  })
})
