import { useEffect, useMemo, useSyncExternalStore } from 'react'
import { uploadApi } from '../api/endpoints'
import { UploadQueue, summarize } from './queue'
import { xhrTransport } from './transport'

// Kept for the page's lifetime: switching galleries mid-upload must not orphan a queue.
const queues = new Map<string, UploadQueue>()

function queueFor(gallery: string, maxPartsPerSign?: number): UploadQueue {
  let queue = queues.get(gallery)
  if (!queue) {
    queue = new UploadQueue({ gallery, api: uploadApi, transport: xhrTransport, maxPartsPerSign })
    queues.set(gallery, queue)
  }
  return queue
}

/** Binds a gallery's queue to React. Warns before leaving while bytes are still moving. */
export function useUploadQueue(gallery: string, maxPartsPerSign?: number) {
  const queue = queueFor(gallery, maxPartsPerSign)
  const items = useSyncExternalStore(queue.subscribe, queue.getSnapshot)
  const summary = useMemo(() => summarize(items), [items])
  const pending = summary.active > 0 || items.some((i) => i.status === 'queued')

  useEffect(() => {
    if (!pending) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [pending])

  return { queue, items, summary }
}
