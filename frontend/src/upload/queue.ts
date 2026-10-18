// Upload queue: a few files at a time, each retried on its own, state kept per file so the
// dashboard can say "12 of 400 failed — retry those 12" (spec FR-12, plan §3.2).
//
// The server owns the upload lifecycle (quota, multipart creation, assembly, verification);
// this only moves bytes and calls the right endpoint at each step.

import { ApiError } from '../api/client'
import type { UploadApi } from '../api/endpoints'
import type { StartedUpload } from '../api/types'
import { TransportError, type Transport } from './transport'

export type UploadStatus = 'queued' | 'uploading' | 'completing' | 'done' | 'failed' | 'cancelled'

export interface UploadItem {
  readonly id: string
  readonly file: File
  readonly contentType: string
  readonly status: UploadStatus
  readonly uploadedBytes: number
  readonly photoId?: string
  readonly error?: string
}

export interface UploadSummary {
  total: number
  done: number
  failed: number
  active: number
  uploadedBytes: number
  totalBytes: number
}

export interface QueueOptions {
  gallery: string
  api: UploadApi
  transport: Transport
  /** Files uploading at once. 3 keeps mobile links from stalling every file at once. */
  concurrency?: number
  /** Wait before each retry; its length is the retry count. */
  retryDelaysMs?: number[]
  maxPartsPerSign?: number
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>
}

const DEFAULT_RETRY_DELAYS_MS = [1_000, 3_000, 10_000, 30_000]
const CANCELLED = () => new DOMException('Upload cancelled', 'AbortError')

function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(CANCELLED())
    const timer = setTimeout(resolve, ms)
    signal.addEventListener('abort', () => {
      clearTimeout(timer)
      reject(CANCELLED())
    }, { once: true })
  })
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

/** Worth retrying: the network or the server hiccupped, not a problem with the request. */
export function isRetryable(error: unknown): boolean {
  const status =
    error instanceof ApiError || error instanceof TransportError ? error.status : undefined
  return status !== undefined && (status === 0 || status === 408 || status === 429 || status >= 500)
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong.'
}

export function summarize(items: readonly UploadItem[]): UploadSummary {
  const count = (status: UploadStatus) => items.filter((i) => i.status === status).length
  const live = items.filter((i) => i.status !== 'cancelled')
  return {
    total: live.length,
    done: count('done'),
    failed: count('failed'),
    active: count('uploading') + count('completing'),
    uploadedBytes: live.reduce((sum, i) => sum + i.uploadedBytes, 0),
    totalBytes: live.reduce((sum, i) => sum + i.file.size, 0),
  }
}

let nextId = 0

export class UploadQueue {
  private items: readonly UploadItem[] = []
  private readonly listeners = new Set<() => void>()
  private readonly controllers = new Map<string, AbortController>()
  private readonly opts: Required<QueueOptions>

  constructor(options: QueueOptions) {
    this.opts = {
      concurrency: 3,
      retryDelaysMs: DEFAULT_RETRY_DELAYS_MS,
      maxPartsPerSign: 100,
      sleep: abortableSleep,
      ...options,
    }
  }

  // --- store interface for useSyncExternalStore -------------------------------------------

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getSnapshot = (): readonly UploadItem[] => this.items

  summary(): UploadSummary {
    return summarize(this.items)
  }

  // --- commands --------------------------------------------------------------------------

  add(files: { file: File; contentType: string }[]): void {
    const added = files.map(({ file, contentType }) => ({
      id: `upload-${++nextId}`,
      file,
      contentType,
      status: 'queued' as const,
      uploadedBytes: 0,
    }))
    this.setItems([...this.items, ...added])
    this.pump()
  }

  retryFailed(): void {
    this.setItems(
      this.items.map((i) =>
        i.status === 'failed' ? { ...i, status: 'queued', error: undefined } : i,
      ),
    )
    this.pump()
  }

  cancel(id: string): void {
    const item = this.find(id)
    if (!item || item.status === 'done' || item.status === 'cancelled') return
    this.controllers.get(id)?.abort()
    this.update(id, { status: 'cancelled' })
    // Release the quota reservation; if this fails the server's expiry sweep catches it.
    if (item.photoId) this.opts.api.abort(item.photoId).catch(() => undefined)
    this.pump()
  }

  clearFinished(): void {
    this.setItems(this.items.filter((i) => i.status !== 'done' && i.status !== 'cancelled'))
  }

  // --- internals -------------------------------------------------------------------------

  private find(id: string): UploadItem | undefined {
    return this.items.find((i) => i.id === id)
  }

  private setItems(items: readonly UploadItem[]): void {
    this.items = items
    this.listeners.forEach((listener) => listener())
  }

  private update(id: string, changes: Partial<UploadItem>): void {
    this.setItems(this.items.map((i) => (i.id === id ? { ...i, ...changes } : i)))
  }

  private pump(): void {
    let running = this.controllers.size
    for (const item of this.items) {
      if (running >= this.opts.concurrency) break
      if (item.status !== 'queued') continue
      running += 1
      void this.run(item.id)
    }
  }

  private async run(id: string): Promise<void> {
    const controller = new AbortController()
    this.controllers.set(id, controller)
    this.update(id, { status: 'uploading' })
    try {
      await this.process(id, controller.signal)
      if (!controller.signal.aborted) this.update(id, { status: 'done' })
    } catch (error) {
      if (!isAbort(error)) this.update(id, { status: 'failed', error: messageOf(error) })
    } finally {
      this.controllers.delete(id)
      this.pump()
    }
  }

  private async withRetry<T>(signal: AbortSignal, step: () => Promise<T>): Promise<T> {
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await step()
      } catch (error) {
        const delay = this.opts.retryDelaysMs[attempt]
        if (signal.aborted) throw CANCELLED()
        if (!isRetryable(error) || delay === undefined) throw error
        await this.opts.sleep(delay, signal)
      }
    }
  }

  private async process(id: string, signal: AbortSignal): Promise<void> {
    const started = await this.target(id, signal)
    if (started === 'already-complete') return
    this.update(id, { photoId: started.photo.id })

    if (started.upload.type === 'single') {
      const { url, headers } = started.upload
      const file = this.find(id)!.file
      await this.withRetry(signal, () =>
        this.opts.transport({
          url,
          headers,
          body: file,
          signal,
          onProgress: (loaded) => this.update(id, { uploadedBytes: loaded }),
        }),
      )
    } else {
      await this.uploadParts(id, started.photo.id, started.upload.part_size, signal)
    }

    if (signal.aborted) throw CANCELLED()
    this.update(id, { status: 'completing', uploadedBytes: this.find(id)!.file.size })
    await this.withRetry(signal, () => this.opts.api.complete(started.photo.id))
  }

  /** A fresh session, or the existing one when retrying, so quota isn't reserved twice. */
  private async target(id: string, signal: AbortSignal): Promise<StartedUpload | 'already-complete'> {
    const item = this.find(id)!
    if (item.photoId) {
      try {
        return await this.withRetry(signal, () => this.opts.api.resume(item.photoId!))
      } catch (error) {
        if (!(error instanceof ApiError) || ![404, 409].includes(error.status)) throw error
        // 409: finished or expired. complete is idempotent, so it tells us which.
        if (error.status === 409 && (await this.tryComplete(item.photoId))) return 'already-complete'
        this.update(id, { photoId: undefined, uploadedBytes: 0 })
      }
    }
    return this.withRetry(signal, () =>
      this.opts.api.start({
        gallery: this.opts.gallery,
        filename: item.file.name,
        contentType: item.contentType,
        size: item.file.size,
      }),
    )
  }

  private async tryComplete(photoId: string): Promise<boolean> {
    try {
      await this.opts.api.complete(photoId)
      return true
    } catch {
      return false
    }
  }

  private async uploadParts(
    id: string,
    photoId: string,
    partSize: number,
    signal: AbortSignal,
  ): Promise<void> {
    const file = this.find(id)!.file
    const partCount = Math.max(1, Math.ceil(file.size / partSize))
    const bounds = (n: number) => [(n - 1) * partSize, Math.min(n * partSize, file.size)] as const

    // Resume: skip parts the bucket already holds in full.
    const stored = await this.withRetry(signal, () => this.opts.api.listParts(photoId))
    const done = new Set(
      stored
        .filter((p) => p.Size === bounds(p.PartNumber)[1] - bounds(p.PartNumber)[0])
        .map((p) => p.PartNumber),
    )
    const remaining = Array.from({ length: partCount }, (_, i) => i + 1).filter((n) => !done.has(n))
    let confirmedBytes = [...done].reduce((sum, n) => sum + bounds(n)[1] - bounds(n)[0], 0)
    this.update(id, { uploadedBytes: confirmedBytes })

    for (let i = 0; i < remaining.length; i += this.opts.maxPartsPerSign) {
      const batch = remaining.slice(i, i + this.opts.maxPartsPerSign)
      let urls = await this.withRetry(signal, () => this.opts.api.signParts(photoId, batch))
      for (const partNumber of batch) {
        const [start, end] = bounds(partNumber)
        let firstTry = true
        await this.withRetry(signal, async () => {
          if (!firstTry) {
            // Signed URLs expire; re-sign before every retry so a long outage can recover.
            urls = { ...urls, ...(await this.opts.api.signParts(photoId, [partNumber])) }
          }
          firstTry = false
          await this.opts.transport({
            url: urls[String(partNumber)],
            body: file.slice(start, end),
            signal,
            onProgress: (loaded) => this.update(id, { uploadedBytes: confirmedBytes + loaded }),
          })
        })
        confirmedBytes += end - start
        this.update(id, { uploadedBytes: confirmedBytes })
      }
    }
  }
}
