import { useEffect, useState } from 'react'
import { galleries as galleriesApi } from '../api/endpoints'
import type { GalleryStats } from '../api/types'

type LoadStats = (galleryId: string) => Promise<GalleryStats>

function Card({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="stat">
      <span className="stat__value">{value}</span>
      <span className="stat__label">{label}</span>
      {hint && <span className="stat__hint">{hint}</span>}
    </div>
  )
}

/** Event performance at a glance (blueprint: views, favourites, orders, gross sales). */
export function StatsPanel({
  galleryId,
  load = galleriesApi.stats,
}: {
  galleryId: string
  load?: LoadStats
}) {
  // The dashboard keys this panel by gallery, so switching galleries starts fresh.
  const [stats, setStats] = useState<GalleryStats | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    load(galleryId).then(
      (data) => !cancelled && setStats(data),
      (err: Error) => !cancelled && setError(err.message),
    )
    return () => {
      cancelled = true
    }
  }, [galleryId, load])

  if (error) return <p className="notice notice--error" role="alert">Stats unavailable: {error}</p>
  if (!stats) return null

  const n = (value: number) => value.toLocaleString()
  const gross = stats.gross.length ? stats.gross.map((g) => g.formatted).join(' + ') : 'UGX 0'
  return (
    <section className="panel stats" aria-labelledby="stats-heading">
      <h2 id="stats-heading">Event overview</h2>
      <div className="stats__grid">
        <Card label="Visitors" value={n(stats.visitors)} hint={`${n(stats.views)} page views`} />
        <Card label="Favourites" value={n(stats.favourites)} />
        <Card
          label="Orders"
          value={n(stats.orders)}
          hint={stats.conversion_percent === null ? undefined : `${stats.conversion_percent}% of visitors`}
        />
        <Card label="Photos sold" value={n(stats.photos_sold)} />
        <Card label="Gross sales" value={gross} hint="Before payment fees" />
      </div>
    </section>
  )
}
