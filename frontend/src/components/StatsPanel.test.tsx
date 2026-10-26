import { render, screen } from '@testing-library/react'
import { ApiError } from '../api/client'
import type { GalleryStats } from '../api/types'
import { StatsPanel } from './StatsPanel'

const stats: GalleryStats = {
  views: 2840,
  visitors: 1210,
  favourites: 312,
  orders: 146,
  photos_sold: 610,
  gross: [
    { currency: 'UGX', amount_minor: 549000, formatted: 'UGX 549,000' },
    { currency: 'USD', amount_minor: 240, formatted: '$2.40' },
  ],
  conversion_percent: 12.1,
}

test('shows visitors, favourites, orders and gross sales per currency', async () => {
  const load = vi.fn(async () => stats)
  render(<StatsPanel galleryId="g1" load={load} />)

  expect(await screen.findByText('1,210')).toBeInTheDocument()
  expect(screen.getByText('2,840 page views')).toBeInTheDocument()
  expect(screen.getByText('312')).toBeInTheDocument()
  expect(screen.getByText('12.1% of visitors')).toBeInTheDocument()
  expect(screen.getByText('UGX 549,000 + $2.40')).toBeInTheDocument()
  expect(screen.getByText('Before payment fees')).toBeInTheDocument()
  expect(load).toHaveBeenCalledWith('g1')
})

test('a new gallery shows zero sales and no conversion yet', async () => {
  const empty = { ...stats, visitors: 0, views: 0, orders: 0, gross: [], conversion_percent: null }
  render(<StatsPanel galleryId="g1" load={async () => empty} />)

  expect(await screen.findByText('UGX 0')).toBeInTheDocument()
  expect(screen.queryByText(/of visitors/)).not.toBeInTheDocument()
})

test('a failure says so instead of showing zeros', async () => {
  const load = async () => Promise.reject(new ApiError(500, 'The server had a problem.'))
  render(<StatsPanel galleryId="g1" load={load} />)

  expect(await screen.findByRole('alert')).toHaveTextContent('Stats unavailable')
})
