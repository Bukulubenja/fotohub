import { useRef, useState, type DragEvent } from 'react'
import type { UploadConfig } from '../api/types'
import { formatBytes, plural } from '../format'
import type { UploadItem, UploadQueue, UploadSummary } from '../upload/queue'
import { screenFiles, type Rejected } from '../upload/screenFiles'

const STATUS_LABEL: Record<UploadItem['status'], string> = {
  queued: 'Waiting',
  uploading: 'Uploading',
  completing: 'Finishing',
  done: 'Uploaded',
  failed: 'Failed',
  cancelled: 'Cancelled',
}
// Problems first, then work in progress; finished files collapse into a count.
const ORDER: UploadItem['status'][] = ['failed', 'uploading', 'completing', 'queued']

interface Props {
  queue: UploadQueue
  items: readonly UploadItem[]
  summary: UploadSummary
  config: UploadConfig
}

export function UploadPanel({ queue, items, summary, config }: Props) {
  const input = useRef<HTMLInputElement>(null)
  const [rejected, setRejected] = useState<Rejected[]>([])
  const [dragging, setDragging] = useState(false)

  const addFiles = (files: File[]) => {
    const screened = screenFiles(files, config)
    setRejected(screened.rejected)
    if (screened.accepted.length) queue.add(screened.accepted)
  }

  const onDrop = (event: DragEvent) => {
    event.preventDefault()
    setDragging(false)
    addFiles(Array.from(event.dataTransfer.files))
  }

  const visible = ORDER.flatMap((status) => items.filter((i) => i.status === status))
  const percent = summary.totalBytes ? Math.round((summary.uploadedBytes / summary.totalBytes) * 100) : 0
  const busy = summary.active > 0 || items.some((i) => i.status === 'queued')

  return (
    <section className="panel" aria-label="Upload photos">
      <div
        className={`dropzone${dragging ? ' dropzone--active' : ''}`}
        onDragOver={(event) => {
          event.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <p className="dropzone__title">Drop photos here</p>
        <p className="muted">JPEG, PNG, WebP or HEIC, up to {formatBytes(config.max_file_bytes)} each</p>
        <button type="button" className="button" onClick={() => input.current?.click()}>
          Choose photos
        </button>
        <input
          ref={input}
          type="file"
          multiple
          hidden
          accept={[...config.allowed_content_types, '.heic', '.heif'].join(',')}
          onChange={(event) => {
            addFiles(Array.from(event.target.files ?? []))
            event.target.value = '' // allow choosing the same files again
          }}
          data-testid="file-input"
        />
      </div>

      {rejected.length > 0 && (
        <div className="notice notice--warn" role="alert">
          <strong>{plural(rejected.length, 'file')} skipped</strong>
          <ul>
            {rejected.slice(0, 5).map((r) => (
              <li key={r.name}>
                {r.name}: {r.reason}
              </li>
            ))}
          </ul>
          {rejected.length > 5 && <p>and {rejected.length - 5} more</p>}
        </div>
      )}

      {summary.total > 0 && (
        <div className="summary">
          <div className="summary__row">
            <span>
              {summary.done.toLocaleString()} of {plural(summary.total, 'photo')} uploaded
              {busy && ` · ${formatBytes(summary.uploadedBytes)} of ${formatBytes(summary.totalBytes)}`}
            </span>
            {!busy && summary.done > 0 && (
              <button type="button" className="link" onClick={() => queue.clearFinished()}>
                Clear finished
              </button>
            )}
          </div>
          <progress max={100} value={percent} aria-label="Overall upload progress" />
        </div>
      )}

      {summary.failed > 0 && (
        <div className="notice notice--error" role="status">
          <span>
            {summary.failed.toLocaleString()} of {plural(summary.total, 'photo')} failed
          </span>
          <button type="button" className="button button--small" onClick={() => queue.retryFailed()}>
            Retry {summary.failed === 1 ? 'it' : `those ${summary.failed.toLocaleString()}`}
          </button>
        </div>
      )}

      {visible.length > 0 && (
        <ul className="files">
          {visible.map((item) => (
            <li key={item.id} className={`file file--${item.status}`}>
              <div className="file__main">
                <span className="file__name">{item.file.name}</span>
                <span className="file__meta">
                  {STATUS_LABEL[item.status]}
                  {item.status === 'uploading' &&
                    ` · ${Math.round((item.uploadedBytes / item.file.size) * 100)}%`}
                  {item.error && ` · ${item.error}`}
                </span>
              </div>
              <button
                type="button"
                className="link"
                onClick={() => queue.cancel(item.id)}
                aria-label={`Cancel ${item.file.name}`}
              >
                Cancel
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
