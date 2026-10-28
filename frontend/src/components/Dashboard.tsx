import { useCallback, useEffect, useState } from 'react'
import { auth, collectionsApi, galleries as galleriesApi, photos, storage, uploadConfig } from '../api/endpoints'
import type { Collection, Gallery, Me, StorageUsage, Subscription, UploadConfig } from '../api/types'
import { BRAND_NAME, BRAND_TAGLINE } from '../brand'
import { formatBytes, plural } from '../format'
import { useUploadQueue } from '../upload/useUploadQueue'
import { BillingPanel } from './BillingPanel'
import { CollectionsPanel } from './CollectionsPanel'
import { NewEventForm } from './NewEventForm'
import { PublishControl } from './PublishControl'
import { StatsPanel } from './StatsPanel'
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
  const [creating, setCreating] = useState(false)
  const [collections, setCollections] = useState<Collection[]>([])
  const [showCollections, setShowCollections] = useState(false)
  // A state setter is stable, so BillingPanel doesn't refetch on every render.
  const [subscription, setSubscription] = useState<Subscription | null>(null)
  const uploadsPaused = subscription !== null && !subscription.can_upload

  const refreshUsage = useCallback(() => {
    storage.usage().then(setUsage, () => undefined)
  }, [])

  // Optional extra: a failure here leaves the collection field out, nothing else.
  const refreshCollections = useCallback(() => {
    collectionsApi.list().then(setCollections, () => undefined)
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
    refreshCollections()
  }, [refreshUsage, refreshCollections])

  const replaceGallery = useCallback((updated: Gallery) => {
    setGalleryList((list) => list.map((g) => (g.id === updated.id ? updated : g)))
  }, [])

  const addGallery = (created: Gallery) => {
    setGalleryList((list) => [created, ...list])
    setSelected(created.id)
    setCreating(false)
    refreshCollections() // event counts
  }

  const addCollection = (created: Collection) => setCollections((list) => [...list, created])
  const replaceCollection = (updated: Collection) =>
    setCollections((list) => list.map((c) => (c.id === updated.id ? updated : c)))

  const logout = async () => {
    await auth.logout().catch(() => undefined)
    onLogout()
  }

  const gallery = galleryList.find((g) => g.id === selected)

  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">
          <strong>{BRAND_NAME}</strong>
          <span className="brand__tagline">{BRAND_TAGLINE}</span>
        </span>
        <StorageMeter usage={usage} />
        <span className="muted">{me.display_name}</span>
        <button type="button" className="link" aria-expanded={showCollections} onClick={() => setShowCollections((v) => !v)}>
          Collections
        </button>
        <button type="button" className="link" onClick={logout}>
          Sign out
        </button>
      </header>

      <main className="content">
        <BillingPanel onChange={setSubscription} />
        {error && <p className="notice notice--error" role="alert">{error}</p>}
        {showCollections && (
          <CollectionsPanel collections={collections} onCreated={addCollection} onUpdated={replaceCollection} />
        )}
        {creating && (
          <NewEventForm collections={collections} onCreated={addGallery} onCancel={() => setCreating(false)} />
        )}
        {config && galleryList.length === 0 && !error && !creating && (
          <section className="panel empty">
            <p className="muted">Create your first event: you get a private gallery to upload photos to and share with guests.</p>
            <button type="button" className="button" onClick={() => setCreating(true)}>
              New event
            </button>
          </section>
        )}
        {galleryList.length > 0 && (
          <div className="gallery-bar">
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
            {!creating && (
              <button type="button" className="button button--small" onClick={() => setCreating(true)}>
                New event
              </button>
            )}
          </div>
        )}
        {gallery && <PublishControl gallery={gallery} onChange={replaceGallery} />}
        {/* Its own key prefix: GalleryUploads below is keyed by the bare id. */}
        {gallery && <StatsPanel key={`stats-${gallery.id}`} galleryId={gallery.id} />}
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
