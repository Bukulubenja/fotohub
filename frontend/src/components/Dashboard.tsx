import { useCallback, useEffect, useState } from 'react'
import { auth, galleries as galleriesApi, photos, storage, uploadConfig } from '../api/endpoints'
import type { Gallery, Me, StorageUsage, Subscription, UploadConfig } from '../api/types'
import { formatBytes, plural } from '../format'
import { useUploadQueue } from '../upload/useUploadQueue'
import { BillingPanel } from './BillingPanel'
import { SharePanel } from './SharePanel'
import { UploadPanel } from './UploadPanel'

const UPLOADABLE = new Set(['draft', 'processing', 'published'])
const PROCESSING_POLL_MS = 5_000

function StorageMeter({ usage }: { usage: StorageUsage | null }) {
  if (!usage) return null
  const level = usage.warning_level ?? 0
  return (
    <div className={`meter${level >= 90 ? ' meter--alert' : level >= 75 ? ' meter--warn' : ''}`}>
      <span>
        {formatBytes(usage.used_bytes)} of {formatBytes(usage.quota_bytes)}
      </span>
      <progress max={100} value={usage.percent} aria-label="Storage used" />
    </div>
  )
}

/** Server-side progress after upload: resizing happens in the background (Celery). */
function useProcessingCounts(gallery: string, refreshKey: number) {
  const [counts, setCounts] = useState({ processing: 0, failed: 0 })
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const load = async () => {
      try {
        const [processing, failed] = await Promise.all([
          photos.count(gallery, 'processing'),
          photos.count(gallery, 'failed'),
        ])
        if (cancelled) return
        setCounts({ processing, failed })
        if (processing > 0) timer = setTimeout(load, PROCESSING_POLL_MS)
      } catch {
        if (!cancelled) timer = setTimeout(load, PROCESSING_POLL_MS * 2)
      }
    }
    void load()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [gallery, refreshKey])
  return counts
}

function GalleryUploads({ gallery, config, onUploaded }: {
  gallery: Gallery
  config: UploadConfig
  onUploaded: () => void
}) {
  const { queue, items, summary } = useUploadQueue(gallery.id, config.max_parts_per_sign_request)
  const counts = useProcessingCounts(gallery.id, summary.done)

  useEffect(() => {
    if (summary.done > 0) onUploaded()
  }, [summary.done, onUploaded])

  return (
    <>
      <SharePanel gallery={gallery} />
      <UploadPanel queue={queue} items={items} summary={summary} config={config} />
      {(counts.processing > 0 || counts.failed > 0) && (
        <p className="muted processing" role="status">
          {counts.processing > 0 && `Preparing ${plural(counts.processing, 'photo')} for the gallery…`}
          {counts.processing > 0 && counts.failed > 0 && ' · '}
          {counts.failed > 0 &&
            `${plural(counts.failed, 'photo')} couldn't be processed. Re-upload the original files.`}
        </p>
      )}
    </>
  )
}

export function Dashboard({ me, onLogout }: { me: Me; onLogout: () => void }) {
  const [galleryList, setGalleryList] = useState<Gallery[]>([])
  const [selected, setSelected] = useState('')
  const [config, setConfig] = useState<UploadConfig | null>(null)
  const [usage, setUsage] = useState<StorageUsage | null>(null)
  const [error, setError] = useState('')
  // A state setter is stable, so BillingPanel doesn't refetch on every render.
  const [subscription, setSubscription] = useState<Subscription | null>(null)
  const uploadsPaused = subscription !== null && !subscription.can_upload

  const refreshUsage = useCallback(() => {
    storage.usage().then(setUsage, () => undefined)
  }, [])

  useEffect(() => {
    Promise.all([galleriesApi.list(), uploadConfig()])
      .then(([page, cfg]) => {
        const open = page.results.filter((g) => UPLOADABLE.has(g.status))
        setGalleryList(open)
        setSelected((current) => current || open[0]?.id || '')
        setConfig(cfg)
      })
      .catch((err: Error) => setError(err.message))
    refreshUsage()
  }, [refreshUsage])

  const logout = async () => {
    await auth.logout().catch(() => undefined)
    onLogout()
  }

  const gallery = galleryList.find((g) => g.id === selected)

  return (
    <div className="shell">
      <header className="topbar">
        <strong>FotoHub</strong>
        <StorageMeter usage={usage} />
        <span className="muted">{me.display_name}</span>
        <button type="button" className="link" onClick={logout}>
          Sign out
        </button>
      </header>

      <main className="content">
        <BillingPanel onChange={setSubscription} />
        {error && <p className="notice notice--error" role="alert">{error}</p>}
        {config && galleryList.length === 0 && !error && (
          <p className="panel muted">Create a gallery first, then come back to add photos.</p>
        )}
        {galleryList.length > 0 && (
          <label className="picker">
            Gallery
            <select value={selected} onChange={(event) => setSelected(event.target.value)}>
              {galleryList.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.title} ({g.status})
                </option>
              ))}
            </select>
          </label>
        )}
        {gallery && uploadsPaused && (
          <>
            <SharePanel gallery={gallery} />
            <p className="panel muted">Uploads are paused until you renew. Your galleries stay online meanwhile.</p>
          </>
        )}
        {gallery && config && !uploadsPaused && (
          // keyed so switching galleries gets a fresh queue view
          <GalleryUploads key={gallery.id} gallery={gallery} config={config} onUploaded={refreshUsage} />
        )}
      </main>
    </div>
  )
}
