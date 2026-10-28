import { useState, type FormEvent } from 'react'
import { collectionQrUrl, collectionsApi, type CollectionsApi } from '../api/endpoints'
import type { Collection } from '../api/types'
import { plural } from '../format'

const WHATSAPP_SHARE = 'https://wa.me/?text='

function NewCollectionForm({ api, onCreated }: { api: CollectionsApi; onCreated: (c: Collection) => void }) {
  const [name, setName] = useState('')
  const [prefix, setPrefix] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      onCreated(await api.create(name.trim(), prefix.trim()))
      setName('')
      setPrefix('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The collection could not be created.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="collection-form" onSubmit={submit}>
      <div className="new-event__row">
        <label>
          Name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="St. Maria Goretti SS"
            maxLength={200}
            required
          />
        </label>
        <label>
          Passkey start <span className="muted">(optional, 2–8 letters)</span>
          <input
            value={prefix}
            onChange={(e) => setPrefix(e.target.value)}
            placeholder="MGSS"
            maxLength={8}
            autoComplete="off"
          />
        </label>
      </div>
      {error && <p className="notice notice--error" role="alert">{error}</p>}
      <div className="renew-actions">
        <button type="submit" className="button button--small" disabled={busy}>
          {busy ? 'Creating…' : 'Create collection'}
        </button>
      </div>
    </form>
  )
}

function CollectionItem({ collection, api, onUpdated }: {
  collection: Collection
  api: CollectionsApi
  onUpdated: (c: Collection) => void
}) {
  const [copied, setCopied] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(collection.share_message)
      setCopied(true)
    } catch {
      setCopied(false) // clipboard blocked: the passkey stays selectable on screen
    }
  }

  const rotate = async () => {
    setBusy(true)
    setError('')
    try {
      onUpdated(await api.rotatePasskey(collection.id))
      setConfirming(false)
      setCopied(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The passkey could not be changed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="collection">
      <div className="collection__head">
        <strong>{collection.name}</strong>
        <span className="muted">
          {plural(collection.event_count, 'event')}
          {collection.status === 'archived' && ' · archived'}
        </span>
      </div>
      <p className="collection__passkey">
        <span className="muted">Passkey</span> <code>{collection.passkey}</code>
      </p>
      <div className="share-actions">
        <a
          className="button button--small"
          href={WHATSAPP_SHARE + encodeURIComponent(collection.share_message)}
          target="_blank"
          rel="noopener noreferrer"
        >
          Send on WhatsApp
        </a>
        <button type="button" className="link" onClick={copy}>
          {copied ? 'Message copied' : 'Copy message'}
        </button>
        <a className="link" href={collectionQrUrl(collection.id)} target="_blank" rel="noopener">
          QR code
        </a>
        <a className="link" href={`/c/${collection.id}/`} target="_blank" rel="noopener">
          Preview
        </a>
        {!confirming && (
          <button type="button" className="link" onClick={() => setConfirming(true)}>
            Change passkey
          </button>
        )}
      </div>
      {confirming && (
        <div className="notice notice--warn">
          <p>Every phone that remembered the old passkey will be signed out. Share the new one again.</p>
          <div className="renew-actions">
            <button type="button" className="button button--small" onClick={rotate} disabled={busy}>
              {busy ? 'Changing…' : 'Yes, change it'}
            </button>
            <button type="button" className="link" onClick={() => setConfirming(false)}>
              Keep it
            </button>
          </div>
        </div>
      )}
      {error && <p className="notice notice--error" role="alert">{error}</p>}
    </li>
  )
}

/** A group the photographer serves again and again (a school): one passkey opens all its events. */
export function CollectionsPanel({
  collections,
  onCreated,
  onUpdated,
  api = collectionsApi,
}: {
  collections: Collection[]
  onCreated: (collection: Collection) => void
  onUpdated: (collection: Collection) => void
  api?: CollectionsApi
}) {
  return (
    <section className="panel collections" aria-labelledby="collections-heading">
      <h2 id="collections-heading">Collections</h2>
      <p className="muted">
        For a school, church or company you photograph often. Share one passkey once: guests enter it on the
        FotoHub home page and see every event you add to the collection.
      </p>
      {collections.length > 0 && (
        <ul className="collection-list">
          {collections.map((collection) => (
            <CollectionItem key={collection.id} collection={collection} api={api} onUpdated={onUpdated} />
          ))}
        </ul>
      )}
      <NewCollectionForm api={api} onCreated={onCreated} />
    </section>
  )
}
