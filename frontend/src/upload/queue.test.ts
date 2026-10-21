import { ApiError } from '../api/client'
import type { UploadApi } from '../api/endpoints'
import type { StartedUpload } from '../api/types'
import { DEFAULT_RETRY_DELAYS_MS, UploadQueue } from './queue'
import { TransportError, type PutRequest } from './transport'

const MiB = 1024 * 1024

function file(name: string, size: number): File {
  return new File([new Uint8Array(size)], name, { type: 'image/jpeg' })
}

function single(photoId: string): StartedUpload {
  return {
    photo: { id: photoId } as StartedUpload['photo'],
    upload: {
      type: 'single',
      method: 'PUT',
      url: `https://bucket/${photoId}`,
      headers: { 'Content-Type': 'image/jpeg' },
      expires_at: '',
    },
  }
}

function multipart(photoId: string, partSize: number, partCount: number): StartedUpload {
  return {
    photo: { id: photoId } as StartedUpload['photo'],
    upload: { type: 'multipart', part_size: partSize, part_count: partCount, expires_at: '' },
  }
}

function fakeApi(overrides: Partial<UploadApi> = {}): UploadApi {
  let n = 0
  return {
    start: vi.fn(async () => single(`photo-${++n}`)),
    resume: vi.fn(async (id: string) => single(id)),
    signParts: vi.fn(async (_id: string, parts: number[]) =>
      Object.fromEntries(parts.map((p) => [String(p), `https://bucket/part-${p}`])),
    ),
    listParts: vi.fn(async () => []),
    complete: vi.fn(async () => ({}) as never),
    abort: vi.fn(async () => undefined),
    ...overrides,
  }
}

const okTransport = vi.fn(async (req: PutRequest) => req.onProgress(req.body.size))
const noSleep = async () => undefined

function makeQueue(api: UploadApi, transport = okTransport, concurrency = 3) {
  return new UploadQueue({ gallery: 'g1', api, transport, concurrency, sleep: noSleep })
}

async function settled(queue: UploadQueue) {
  await vi.waitFor(() => {
    const busy = queue.getSnapshot().some((i) => ['queued', 'uploading', 'completing'].includes(i.status))
    if (busy) throw new Error('still uploading')
  })
}

beforeEach(() => {
  okTransport.mockClear()
})

test('uploads a small file with one PUT and completes it', async () => {
  const api = fakeApi()
  const queue = makeQueue(api)

  queue.add([{ file: file('a.jpg', 100), contentType: 'image/jpeg' }])
  await settled(queue)

  const [item] = queue.getSnapshot()
  expect(item.status).toBe('done')
  expect(item.uploadedBytes).toBe(100)
  expect(api.start).toHaveBeenCalledWith({
    gallery: 'g1',
    filename: 'a.jpg',
    contentType: 'image/jpeg',
    size: 100,
  })
  expect(okTransport.mock.calls[0][0].headers).toEqual({ 'Content-Type': 'image/jpeg' })
  expect(api.complete).toHaveBeenCalledWith('photo-1')
})

test('uploads a large file in parts, slicing exactly at part boundaries', async () => {
  const api = fakeApi({ start: vi.fn(async () => multipart('big', 5 * MiB, 3)) })
  const queue = makeQueue(api)

  queue.add([{ file: file('big.jpg', 12 * MiB), contentType: 'image/jpeg' }])
  await settled(queue)

  expect(queue.getSnapshot()[0].status).toBe('done')
  expect(api.signParts).toHaveBeenCalledWith('big', [1, 2, 3])
  expect(okTransport.mock.calls.map((c) => c[0].body.size)).toEqual([5 * MiB, 5 * MiB, 2 * MiB])
  expect(api.complete).toHaveBeenCalledWith('big')
})

test('signs parts in batches no larger than the server allows', async () => {
  const api = fakeApi({ start: vi.fn(async () => multipart('big', MiB, 5)) })
  const queue = new UploadQueue({
    gallery: 'g1', api, transport: okTransport, sleep: noSleep, maxPartsPerSign: 2,
  })

  queue.add([{ file: file('big.jpg', 5 * MiB), contentType: 'image/jpeg' }])
  await settled(queue)

  expect(vi.mocked(api.signParts).mock.calls.map((c) => c[1])).toEqual([[1, 2], [3, 4], [5]])
})

test('resumes a multipart upload by skipping parts already stored in full', async () => {
  const api = fakeApi({
    start: vi.fn(async () => multipart('big', 5 * MiB, 3)),
    listParts: vi.fn(async () => [
      { PartNumber: 1, Size: 5 * MiB },
      { PartNumber: 2, Size: 1234 }, // partial: must be re-sent
    ]),
  })
  const queue = makeQueue(api)

  queue.add([{ file: file('big.jpg', 12 * MiB), contentType: 'image/jpeg' }])
  await settled(queue)

  expect(api.signParts).toHaveBeenCalledWith('big', [2, 3])
  expect(okTransport).toHaveBeenCalledTimes(2)
})

test('retries network failures and storage 5xx, then succeeds', async () => {
  const flaky = vi
    .fn<(req: PutRequest) => Promise<void>>()
    .mockRejectedValueOnce(new TransportError(0, 'offline'))
    .mockRejectedValueOnce(new TransportError(503, 'busy'))
    .mockResolvedValue(undefined)
  const queue = makeQueue(fakeApi(), flaky)

  queue.add([{ file: file('a.jpg', 10), contentType: 'image/jpeg' }])
  await settled(queue)

  expect(queue.getSnapshot()[0].status).toBe('done')
  expect(flaky).toHaveBeenCalledTimes(3)
})

test('re-signs a part before retrying it, since signed URLs expire', async () => {
  const api = fakeApi({ start: vi.fn(async () => multipart('big', MiB, 1)) })
  const flaky = vi
    .fn<(req: PutRequest) => Promise<void>>()
    .mockRejectedValueOnce(new TransportError(500, 'storage hiccup'))
    .mockResolvedValue(undefined)
  const queue = makeQueue(api, flaky)

  queue.add([{ file: file('big.jpg', MiB), contentType: 'image/jpeg' }])
  await settled(queue)

  expect(vi.mocked(api.signParts).mock.calls.map((c) => c[1])).toEqual([[1], [1]])
})

test('does not retry a rejected request and reports the server message', async () => {
  const api = fakeApi({
    start: vi.fn(async () => {
      throw new ApiError(409, 'This upload would go over your storage limit.')
    }),
  })
  const queue = makeQueue(api)

  queue.add([{ file: file('a.jpg', 10), contentType: 'image/jpeg' }])
  await settled(queue)

  const [item] = queue.getSnapshot()
  expect(item.status).toBe('failed')
  expect(item.error).toBe('This upload would go over your storage limit.')
  expect(api.start).toHaveBeenCalledTimes(1)
})

test('gives up after the last retry delay', async () => {
  const down = vi.fn(async () => {
    throw new TransportError(0, 'Connection lost while uploading.')
  })
  const queue = new UploadQueue({
    gallery: 'g1', api: fakeApi(), transport: down, sleep: noSleep, retryDelaysMs: [1, 1],
  })

  queue.add([{ file: file('a.jpg', 10), contentType: 'image/jpeg' }])
  await settled(queue)

  expect(queue.getSnapshot()[0].status).toBe('failed')
  expect(down).toHaveBeenCalledTimes(3)
})

test('time offline does not use up retries', async () => {
  let online = false
  let calls = 0
  const transport = vi.fn(async () => {
    calls += 1
    if (calls <= 5) throw new TransportError(0, 'Connection lost while uploading.')
  })
  const network = {
    isOnline: () => online,
    waitForOnline: vi.fn(async () => {
      if (calls >= 5) online = true // the connection comes back after a long outage
    }),
  }
  const queue = new UploadQueue({
    gallery: 'g1', api: fakeApi(), transport, sleep: noSleep, retryDelaysMs: [1], network,
  })

  queue.add([{ file: file('a.jpg', 10), contentType: 'image/jpeg' }])
  await settled(queue)

  expect(queue.getSnapshot()[0].status).toBe('done')
  expect(transport).toHaveBeenCalledTimes(6) // 5 failures while offline, none counted
  expect(network.waitForOnline).toHaveBeenCalled()
})

test('default schedule keeps retrying for several minutes', () => {
  const total = DEFAULT_RETRY_DELAYS_MS.reduce((sum, ms) => sum + ms, 0)
  expect(total).toBeGreaterThanOrEqual(5 * 60_000)
})

test('retrying a failed file reuses its server session instead of reserving again', async () => {
  let fail = true
  const transport = vi.fn(async () => {
    if (fail) throw new TransportError(400, 'bad request')
  })
  const api = fakeApi()
  const queue = makeQueue(api, transport)
  queue.add([{ file: file('a.jpg', 10), contentType: 'image/jpeg' }])
  await settled(queue)

  fail = false
  queue.retryFailed()
  await settled(queue)

  expect(queue.getSnapshot()[0].status).toBe('done')
  expect(api.start).toHaveBeenCalledTimes(1)
  expect(api.resume).toHaveBeenCalledWith('photo-1')
})

test('retry starts a fresh session when the old one expired', async () => {
  let fail = true
  const transport = vi.fn(async () => {
    if (fail) throw new TransportError(400, 'bad request')
  })
  const api = fakeApi({
    resume: vi.fn(async () => {
      throw new ApiError(409, 'This upload expired.')
    }),
    complete: vi
      .fn()
      .mockRejectedValueOnce(new ApiError(409, 'This upload expired.'))
      .mockResolvedValue({}),
  })
  const queue = makeQueue(api, transport)
  queue.add([{ file: file('a.jpg', 10), contentType: 'image/jpeg' }])
  await settled(queue)

  fail = false
  queue.retryFailed()
  await settled(queue)

  expect(queue.getSnapshot()[0].status).toBe('done')
  expect(api.start).toHaveBeenCalledTimes(2)
})

test('retry finishes without re-uploading when the server already completed it', async () => {
  let fail = true
  const api = fakeApi({
    resume: vi.fn(async () => {
      throw new ApiError(409, 'This upload has already finished.')
    }),
    complete: vi.fn(async () => {
      if (fail) throw new ApiError(400, 'flaky')
      return {} as never
    }),
  })
  const queue = makeQueue(api)
  queue.add([{ file: file('a.jpg', 10), contentType: 'image/jpeg' }])
  await settled(queue)
  okTransport.mockClear()

  fail = false
  queue.retryFailed()
  await settled(queue)

  expect(queue.getSnapshot()[0].status).toBe('done')
  expect(okTransport).not.toHaveBeenCalled()
})

test('never runs more files at once than the concurrency limit', async () => {
  let inFlight = 0
  let peak = 0
  const slow = vi.fn(async () => {
    inFlight += 1
    peak = Math.max(peak, inFlight)
    await new Promise((resolve) => setTimeout(resolve, 5))
    inFlight -= 1
  })
  const queue = makeQueue(fakeApi(), slow, 2)

  queue.add(Array.from({ length: 6 }, (_, i) => ({ file: file(`${i}.jpg`, 10), contentType: 'image/jpeg' })))
  await settled(queue)

  expect(peak).toBe(2)
  expect(queue.summary()).toMatchObject({ total: 6, done: 6, failed: 0 })
})

test('cancel stops the transfer and releases the reservation', async () => {
  const api = fakeApi()
  const hanging = vi.fn(
    (req: PutRequest) =>
      new Promise<void>((_, reject) =>
        req.signal.addEventListener('abort', () => reject(new DOMException('x', 'AbortError'))),
      ),
  )
  const queue = makeQueue(api, hanging)
  queue.add([{ file: file('a.jpg', 10), contentType: 'image/jpeg' }])
  await vi.waitFor(() => expect(hanging).toHaveBeenCalled())

  queue.cancel(queue.getSnapshot()[0].id)
  await settled(queue)

  expect(queue.getSnapshot()[0].status).toBe('cancelled')
  expect(api.abort).toHaveBeenCalledWith('photo-1')
  expect(api.complete).not.toHaveBeenCalled()
})

test('summary counts failures for the retry banner', async () => {
  const transport = vi.fn(async (req: PutRequest) => {
    if (req.url.endsWith('photo-2')) throw new TransportError(400, 'nope')
  })
  const queue = makeQueue(fakeApi(), transport, 1)

  queue.add(['a', 'b', 'c'].map((n) => ({ file: file(`${n}.jpg`, 10), contentType: 'image/jpeg' })))
  await settled(queue)

  expect(queue.summary()).toMatchObject({ total: 3, done: 2, failed: 1, active: 0 })
})
