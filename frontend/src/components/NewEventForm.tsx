import { useState, type FormEvent } from 'react'
import { galleryAdmin, type GalleryAdminApi } from '../api/endpoints'
import type { EventInfo, Gallery } from '../api/types'

function today(): string {
  const now = new Date()
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 10)
}

/** A gallery link stops working at the end of its expiry day, local time. */
function endOfDay(date: string): string {
  return new Date(`${date}T23:59:59`).toISOString()
}

/** One step for the photographer: the event, and the private gallery guests will open. */
export function NewEventForm({
  onCreated,
  onCancel,
  api = galleryAdmin,
}: {
  onCreated: (gallery: Gallery) => void
  onCancel: () => void
  api?: GalleryAdminApi
}) {
  const [title, setTitle] = useState('')
  const [date, setDate] = useState(today)
  const [location, setLocation] = useState('')
  const [description, setDescription] = useState('')
  const [password, setPassword] = useState('')
  const [expires, setExpires] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // Kept after a failed gallery step, so retrying doesn't create the event twice.
  const [event, setEvent] = useState<EventInfo | null>(null)

  const submit = async (formEvent: FormEvent) => {
    formEvent.preventDefault()
    setBusy(true)
    setError('')
    try {
      const created =
        event ??
        (await api.createEvent({
          title: title.trim(),
          event_date: date,
          location_label: location.trim(),
          description: description.trim(),
        }))
      setEvent(created)
      const gallery = await api.createGallery({
        event: created.id,
        title: created.title,
        ...(password ? { password } : {}),
        expires_at: expires ? endOfDay(expires) : null,
      })
      onCreated(gallery)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The event could not be created.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel new-event" aria-labelledby="new-event-heading">
      <h2 id="new-event-heading">New event</h2>
      <p className="muted">Guests get a private gallery link. It stays a draft, visible only to you, until you publish it.</p>
      <form onSubmit={submit}>
        <label>
          Event name
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Prom Night 2026"
            maxLength={200}
            required
            disabled={event !== null}
          />
        </label>
        <div className="new-event__row">
          <label>
            Date
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} required disabled={event !== null} />
          </label>
          <label>
            Place <span className="muted">(optional)</span>
            <input
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="Kampala Serena"
              maxLength={200}
              disabled={event !== null}
            />
          </label>
        </div>
        <label>
          Note for guests <span className="muted">(optional)</span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            disabled={event !== null}
          />
        </label>
        <div className="new-event__row">
          <label>
            Gallery password <span className="muted">(optional)</span>
            <input
              type="text"
              autoComplete="off"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              maxLength={128}
            />
          </label>
          <label>
            Link stops working after <span className="muted">(optional)</span>
            <input type="date" value={expires} min={today()} onChange={(e) => setExpires(e.target.value)} />
          </label>
        </div>
        {error && <p className="notice notice--error" role="alert">{error}</p>}
        <div className="renew-actions">
          <button type="submit" className="button" disabled={busy}>
            {busy ? 'Creating…' : event ? 'Try again' : 'Create event'}
          </button>
          <button type="button" className="link" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </form>
    </section>
  )
}
