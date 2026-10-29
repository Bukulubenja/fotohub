import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Mock } from 'vitest'
import type { GalleryAdminApi } from '../api/endpoints'
import { ApiError } from '../api/client'
import type { Collection, EventInfo, Gallery } from '../api/types'
import { NewEventForm } from './NewEventForm'

const event: EventInfo = {
  id: 'e1',
  title: 'Prom Night 2026',
  event_date: '2026-11-07',
  description: '',
  location_label: 'Kampala Serena',
  collection: null,
  separate_access: false,
}

const gallery: Gallery = {
  id: 'g1',
  event: 'e1',
  title: 'Prom Night 2026',
  status: 'draft',
  has_password: false,
  expires_at: null,
  share_url: 'https://fotohub.ug/g/tok/',
  share_message: 'Hi!',
}

type FakeApi = { [K in keyof GalleryAdminApi]: Mock<GalleryAdminApi[K]> }

function fakeApi(): FakeApi {
  return {
    createEvent: vi.fn(async () => event),
    createGallery: vi.fn(async () => gallery),
    publish: vi.fn(),
    unpublish: vi.fn(),
  }
}

async function fillIn() {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Event name'), '  Prom Night 2026 ')
  await user.type(screen.getByLabelText(/Place/), 'Kampala Serena')
  return user
}

test('creates the event, then its gallery, and hands the gallery back', async () => {
  const api = fakeApi()
  const onCreated = vi.fn()
  render(<NewEventForm api={api} onCreated={onCreated} onCancel={vi.fn()} />)
  const user = await fillIn()
  await user.type(screen.getByLabelText(/Gallery password/), 'amina-2026')

  await user.click(screen.getByRole('button', { name: 'Create event' }))

  expect(api.createEvent).toHaveBeenCalledWith(
    expect.objectContaining({ title: 'Prom Night 2026', location_label: 'Kampala Serena' }),
  )
  expect(api.createGallery).toHaveBeenCalledWith({
    event: 'e1',
    title: 'Prom Night 2026',
    password: 'amina-2026',
    expires_at: null,
  })
  expect(onCreated).toHaveBeenCalledWith(gallery)
})

test('a password shorter than 8 characters is stopped before anything is created', async () => {
  const api = fakeApi()
  render(<NewEventForm api={api} onCreated={vi.fn()} onCancel={vi.fn()} />)
  const user = await fillIn()
  await user.type(screen.getByLabelText(/Gallery password/), '1234')

  await user.click(screen.getByRole('button', { name: 'Create event' }))

  expect(screen.getByRole('alert')).toHaveTextContent('at least 8 characters')
  expect(api.createEvent).not.toHaveBeenCalled()
})

test('no password is sent when the field is empty', async () => {
  const api = fakeApi()
  render(<NewEventForm api={api} onCreated={vi.fn()} onCancel={vi.fn()} />)
  const user = await fillIn()

  await user.click(screen.getByRole('button', { name: 'Create event' }))

  expect(api.createGallery.mock.calls[0][0]).not.toHaveProperty('password')
})

test('an expiry date becomes the end of that day', async () => {
  const api = fakeApi()
  render(<NewEventForm api={api} onCreated={vi.fn()} onCancel={vi.fn()} />)
  const user = await fillIn()
  await user.type(screen.getByLabelText(/Link stops working after/), '2099-12-31')

  await user.click(screen.getByRole('button', { name: 'Create event' }))

  const sent = api.createGallery.mock.calls[0][0].expires_at as string
  expect(new Date(sent).getTime()).toBe(new Date('2099-12-31T23:59:59').getTime())
})

test('a failed gallery step shows the error and retries without a second event', async () => {
  const api = fakeApi()
  api.createGallery.mockRejectedValueOnce(new ApiError(400, 'Expiry must be in the future.'))
  const onCreated = vi.fn()
  render(<NewEventForm api={api} onCreated={onCreated} onCancel={vi.fn()} />)
  const user = await fillIn()

  await user.click(screen.getByRole('button', { name: 'Create event' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('Expiry must be in the future.')
  await user.click(screen.getByRole('button', { name: 'Try again' }))
  expect(api.createEvent).toHaveBeenCalledTimes(1)
  expect(api.createGallery).toHaveBeenCalledTimes(2)
  expect(onCreated).toHaveBeenCalledWith(gallery)
})

const school: Collection = {
  id: 'c1',
  name: 'St. Maria Goretti SS',
  passkey: 'MGSS-7K4Q-X9AB',
  status: 'active',
  event_count: 0,
  subscriber_count: 0,
  share_url: 'https://fotohub.ug/',
  share_message: '',
}

test('without collections there is no collection field', () => {
  render(<NewEventForm api={fakeApi()} onCreated={vi.fn()} onCancel={vi.fn()} />)

  expect(screen.queryByLabelText(/Collection/)).not.toBeInTheDocument()
})

test('an event can join a collection', async () => {
  const api = fakeApi()
  render(<NewEventForm api={api} collections={[school]} onCreated={vi.fn()} onCancel={vi.fn()} />)
  const user = await fillIn()
  await user.selectOptions(screen.getByLabelText(/Collection/), 'c1')

  await user.click(screen.getByRole('button', { name: 'Create event' }))

  expect(api.createEvent).toHaveBeenCalledWith(
    expect.objectContaining({ collection: 'c1', separate_access: false }),
  )
})

test('an event in a collection can keep its own password', async () => {
  const api = fakeApi()
  render(<NewEventForm api={api} collections={[school]} onCreated={vi.fn()} onCancel={vi.fn()} />)
  const user = await fillIn()
  expect(screen.queryByLabelText(/Keep a separate password/)).not.toBeInTheDocument()
  await user.selectOptions(screen.getByLabelText(/Collection/), 'c1')
  await user.click(screen.getByLabelText(/Keep a separate password/))

  await user.click(screen.getByRole('button', { name: 'Create event' }))

  expect(api.createEvent).toHaveBeenCalledWith(
    expect.objectContaining({ collection: 'c1', separate_access: true }),
  )
})

test('archived collections are not offered', () => {
  const archived = { ...school, id: 'c2', name: 'Old school', status: 'archived' as const }
  render(
    <NewEventForm api={fakeApi()} collections={[school, archived]} onCreated={vi.fn()} onCancel={vi.fn()} />,
  )

  expect(screen.queryByRole('option', { name: 'Old school' })).not.toBeInTheDocument()
})
