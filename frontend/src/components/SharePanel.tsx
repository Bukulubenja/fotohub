import { useState } from 'react'
import type { Gallery } from '../api/types'

const WHATSAPP_SHARE = 'https://wa.me/?text='

type Copied = '' | 'link' | 'message'

/** Spec FR-24: one link, and a message ready to paste into WhatsApp. */
export function SharePanel({ gallery }: { gallery: Gallery }) {
  const [copied, setCopied] = useState<Copied>('')

  const copy = async (what: Copied, text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(what)
    } catch {
      setCopied('') // clipboard blocked: the link field stays selectable
    }
  }

  if (gallery.status !== 'published') {
    return (
      <p className="panel muted share">
        Publish this gallery to share it.{' '}
        <a href={gallery.share_url} target="_blank" rel="noopener">
          Preview it as a customer
        </a>
      </p>
    )
  }

  return (
    <section className="panel share" aria-labelledby="share-heading">
      <h2 id="share-heading">Share with your client</h2>
      <div className="share-link">
        <input
          readOnly
          value={gallery.share_url}
          aria-label="Gallery link"
          onFocus={(event) => event.currentTarget.select()}
        />
        <button type="button" className="button button--small" onClick={() => copy('link', gallery.share_url)}>
          {copied === 'link' ? 'Copied' : 'Copy link'}
        </button>
      </div>
      <div className="share-actions">
        <a
          className="button button--small"
          href={WHATSAPP_SHARE + encodeURIComponent(gallery.share_message)}
          target="_blank"
          rel="noopener noreferrer"
        >
          Send on WhatsApp
        </a>
        <button type="button" className="link" onClick={() => copy('message', gallery.share_message)}>
          {copied === 'message' ? 'Message copied' : 'Copy message'}
        </button>
        <a className="link" href={gallery.share_url} target="_blank" rel="noopener">
          Preview
        </a>
      </div>
    </section>
  )
}
