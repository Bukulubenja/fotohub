// Sends bytes straight to object storage. XHR rather than fetch: fetch has no upload progress.

export class TransportError extends Error {
  readonly status: number // 0 = network failure or timeout

  constructor(status: number, message: string) {
    super(message)
    this.name = 'TransportError'
    this.status = status
  }
}

export interface PutRequest {
  url: string
  body: Blob
  headers?: Record<string, string>
  signal: AbortSignal
  onProgress: (loadedBytes: number) => void
}

export type Transport = (request: PutRequest) => Promise<void>

const PART_TIMEOUT_MS = 5 * 60 * 1000 // an 8 MiB part on a slow 3G link

export const xhrTransport: Transport = ({ url, body, headers = {}, signal, onProgress }) =>
  new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', url)
    xhr.timeout = PART_TIMEOUT_MS
    for (const [name, value] of Object.entries(headers)) xhr.setRequestHeader(name, value)

    const onAbort = () => xhr.abort()
    signal.addEventListener('abort', onAbort, { once: true })
    const cleanup = () => signal.removeEventListener('abort', onAbort)

    xhr.upload.onprogress = (event) => onProgress(event.loaded)
    xhr.onload = () => {
      cleanup()
      if (xhr.status >= 200 && xhr.status < 300) resolve()
      else reject(new TransportError(xhr.status, `Storage rejected the upload (${xhr.status}).`))
    }
    xhr.onerror = () => {
      cleanup()
      reject(new TransportError(0, 'Connection lost while uploading.'))
    }
    xhr.ontimeout = () => {
      cleanup()
      reject(new TransportError(0, 'Upload timed out.'))
    }
    xhr.onabort = () => {
      cleanup()
      reject(new DOMException('Upload cancelled', 'AbortError'))
    }
    xhr.send(body)
  })
