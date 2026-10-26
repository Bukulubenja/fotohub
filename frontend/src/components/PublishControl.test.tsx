import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { GalleryAdminApi } from '../api/endpoints'
import { ApiError } from '../api/client'
import type { Gallery } from '../api/types'
import { PublishControl } from './PublishControl'

function gallery(status: Gallery['status'], overrides: Partial<Gallery> = {}): Gallery {
  return {
    id: 'g1',
    event: 'e1',
    title: 'Prom Night 2026',
    status,
    has_password: false,
    expires_at: null,
    share_url: 'https://fotohub.ug/g/tok/',
    share_message: 'Hi!',
    ...overrides,
  }
}

function fakeApi(): GalleryAdminApi {
  return {
    createEvent: vi.fn(),
    createGallery: vi.fn(),
    publish: vi.fn(async () => gallery('published')),
    unpublish: vi.fn(async () => gallery('draft')),
  }
}

test('a draft can be published', async () => {
  const api = fakeApi()
  const onChange = vi.fn()
  render(<PublishControl gallery={gallery('draft')} api={api} onChange={onChange} />)
  expect(screen.getByRole('status')).toHaveTextContent('Draft: only you can see it.')

  await userEvent.click(screen.getByRole('button', { name: 'Publish gallery' }))

  expect(api.publish).toHaveBeenCalledWith('g1')
  expect(onChange).toHaveBeenCalledWith(gallery('published'))
})

test('a published gallery can be taken down', async () => {
  const api = fakeApi()
  const onChange = vi.fn()
  render(<PublishControl gallery={gallery('published', { has_password: true })} api={api} onChange={onChange} />)
  expect(screen.getByRole('status')).toHaveTextContent('anyone with the link can open it. Guests need the password.')

  await userEvent.click(screen.getByRole('button', { name: 'Unpublish' }))

  expect(api.unpublish).toHaveBeenCalledWith('g1')
  expect(onChange).toHaveBeenCalledWith(gallery('draft'))
})

test('the server explains why publishing failed', async () => {
  const api = fakeApi()
  vi.mocked(api.publish).mockRejectedValueOnce(
    new ApiError(409, 'Set an expiry date in the future before publishing.'),
  )
  const onChange = vi.fn()
  render(<PublishControl gallery={gallery('draft')} api={api} onChange={onChange} />)

  await userEvent.click(screen.getByRole('button', { name: 'Publish gallery' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('Set an expiry date in the future')
  expect(onChange).not.toHaveBeenCalled()
})

test('a processing gallery offers no publish button', () => {
  render(<PublishControl gallery={gallery('processing')} api={fakeApi()} onChange={vi.fn()} />)

  expect(screen.queryByRole('button')).not.toBeInTheDocument()
})
