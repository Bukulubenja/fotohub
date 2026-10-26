import { useState } from 'react'
import { galleryAdmin, type GalleryAdminApi } from '../api/endpoints'
import type { Gallery } from '../api/types'

const STATE_TEXT: Record<string, string> = {
  draft: 'Draft: only you can see it.',
  processing: 'Processing.',
  published: 'Published: anyone with the link can open it.',
  archived: 'Archived.',
  deletion_pending: 'Being deleted.',
}

export function PublishControl({
  gallery,
  onChange,
  api = galleryAdmin,
}: {
  gallery: Gallery
  onChange: (gallery: Gallery) => void
  api?: GalleryAdminApi
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const run = async (action: (id: string) => Promise<Gallery>) => {
    setBusy(true)
    setError('')
    try {
      onChange(await action(gallery.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That didn\'t work. Try again.')
    } finally {
      setBusy(false)
    }
  }

  const published = gallery.status === 'published'
  return (
    <section className={`panel publish publish--${gallery.status}`} aria-label="Gallery status">
      <p role="status">
        <strong>{gallery.title}</strong> · {STATE_TEXT[gallery.status] ?? gallery.status}
        {gallery.has_password && ' Guests need the password.'}
        {gallery.expires_at && ` Link works until ${new Date(gallery.expires_at).toLocaleDateString()}.`}
      </p>
      {(gallery.status === 'draft' || published) && (
        <button
          type="button"
          className={published ? 'button button--small button--quiet' : 'button button--small'}
          disabled={busy}
          onClick={() => run(published ? api.unpublish : api.publish)}
        >
          {published ? 'Unpublish' : 'Publish gallery'}
        </button>
      )}
      {error && <p className="notice notice--error" role="alert">{error}</p>}
    </section>
  )
}
