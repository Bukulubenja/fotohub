import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Mock } from 'vitest'
import type { CollectionsApi } from '../api/endpoints'
import { ApiError } from '../api/client'
import type { Collection } from '../api/types'
import { CollectionsPanel } from './CollectionsPanel'

const school: Collection = {
  id: 'c1',
  name: 'St. Maria Goretti SS',
  passkey: 'MGSS-7K4Q-X9AB',
  status: 'active',
  event_count: 3,
  subscriber_count: 2,
  share_url: 'https://fotohub.ug/',
  share_message: 'Open https://fotohub.ug/ and enter the passkey MGSS-7K4Q-X9AB',
}

type FakeApi = { [K in keyof CollectionsApi]: Mock<CollectionsApi[K]> }

function fakeApi(): FakeApi {
  return {
    list: vi.fn(async () => [school]),
    create: vi.fn(async (name: string) => ({ ...school, id: 'c2', name, event_count: 0 })),
    rotatePasskey: vi.fn(async () => ({ ...school, passkey: 'MGSS-2345-6789' })),
  }
}

function renderPanel(api = fakeApi(), collections = [school]) {
  const onCreated = vi.fn()
  const onUpdated = vi.fn()
  render(
    <CollectionsPanel api={api} collections={collections} onCreated={onCreated} onUpdated={onUpdated} />,
  )
  return { api, onCreated, onUpdated, user: userEvent.setup() }
}

test('shows each collection with its passkey, events and sharing options', () => {
  renderPanel()

  const item = screen.getByRole('listitem')
  expect(within(item).getByText('St. Maria Goretti SS')).toBeInTheDocument()
  expect(within(item).getByText('MGSS-7K4Q-X9AB')).toBeInTheDocument()
  expect(within(item).getByText(/3 events/)).toBeInTheDocument()
  expect(within(item).getByRole('link', { name: 'Send on WhatsApp' })).toHaveAttribute(
    'href',
    `https://wa.me/?text=${encodeURIComponent(school.share_message)}`,
  )
  expect(within(item).getByRole('link', { name: 'QR code' })).toHaveAttribute(
    'href',
    '/api/v1/collections/c1/qr/',
  )
})

test('creates a collection from a name and an optional prefix', async () => {
  const { api, onCreated, user } = renderPanel(fakeApi(), [])

  await user.type(screen.getByLabelText('Name'), '  Kitante Primary  ')
  await user.type(screen.getByLabelText(/Passkey start/), ' kps ')
  await user.click(screen.getByRole('button', { name: 'Create collection' }))

  expect(api.create).toHaveBeenCalledWith('Kitante Primary', 'kps')
  expect(onCreated).toHaveBeenCalledWith(expect.objectContaining({ name: 'Kitante Primary' }))
})

test('shows why a collection could not be created', async () => {
  const api = fakeApi()
  api.create.mockRejectedValueOnce(new ApiError(400, 'Use 2 to 8 letters or digits, like MGSS.'))
  const { onCreated, user } = renderPanel(api, [])

  await user.type(screen.getByLabelText('Name'), 'Kitante Primary')
  await user.type(screen.getByLabelText(/Passkey start/), 'k!')
  await user.click(screen.getByRole('button', { name: 'Create collection' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('Use 2 to 8 letters or digits')
  expect(onCreated).not.toHaveBeenCalled()
})

test('changing the passkey asks first, then replaces it', async () => {
  const { api, onUpdated, user } = renderPanel()

  await user.click(screen.getByRole('button', { name: 'Change passkey' }))
  expect(api.rotatePasskey).not.toHaveBeenCalled()
  expect(screen.getByText(/signed out/)).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: 'Yes, change it' }))

  expect(api.rotatePasskey).toHaveBeenCalledWith('c1')
  expect(onUpdated).toHaveBeenCalledWith(expect.objectContaining({ passkey: 'MGSS-2345-6789' }))
})

test('changing the passkey can be called off', async () => {
  const { api, user } = renderPanel()

  await user.click(screen.getByRole('button', { name: 'Change passkey' }))
  await user.click(screen.getByRole('button', { name: 'Keep it' }))

  expect(api.rotatePasskey).not.toHaveBeenCalled()
  expect(screen.queryByText(/signed out/)).not.toBeInTheDocument()
})
