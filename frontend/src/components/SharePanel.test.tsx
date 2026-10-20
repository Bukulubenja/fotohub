import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Gallery } from '../api/types'
import { SharePanel } from './SharePanel'

function gallery(status: Gallery['status']): Gallery {
  return {
    id: 'g1',
    event: 'e1',
    title: 'Amina & Daniel',
    status,
    share_url: 'https://fotohub.ug/g/tok/',
    share_message: 'Hi! Your photos are ready:\nhttps://fotohub.ug/g/tok/\n— Lens & Light',
  }
}

test('published gallery offers the link and a WhatsApp-ready message', () => {
  render(<SharePanel gallery={gallery('published')} />)

  expect(screen.getByLabelText('Gallery link')).toHaveValue('https://fotohub.ug/g/tok/')
  const whatsapp = screen.getByRole('link', { name: 'Send on WhatsApp' })
  const text = new URL(whatsapp.getAttribute('href')!).searchParams.get('text')
  expect(text).toBe(gallery('published').share_message)
  expect(screen.getByRole('link', { name: 'Preview' })).toHaveAttribute('href', 'https://fotohub.ug/g/tok/')
})

test('copy buttons put the link or the message on the clipboard', async () => {
  const user = userEvent.setup()
  const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
  render(<SharePanel gallery={gallery('published')} />)

  await user.click(screen.getByRole('button', { name: 'Copy link' }))
  expect(writeText).toHaveBeenLastCalledWith('https://fotohub.ug/g/tok/')
  expect(screen.getByRole('button', { name: 'Copied' })).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: 'Copy message' }))
  expect(writeText).toHaveBeenLastCalledWith(gallery('published').share_message)
  expect(screen.getByRole('button', { name: 'Message copied' })).toBeInTheDocument()
})

test('a blocked clipboard leaves the buttons usable', async () => {
  const user = userEvent.setup()
  vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'))
  render(<SharePanel gallery={gallery('published')} />)

  await user.click(screen.getByRole('button', { name: 'Copy link' }))

  expect(screen.getByRole('button', { name: 'Copy link' })).toBeInTheDocument()
})

test('draft gallery explains publishing and links to a preview', () => {
  render(<SharePanel gallery={gallery('draft')} />)

  expect(screen.getByText(/Publish this gallery to share it/)).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Send on WhatsApp' })).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Preview it as a customer' })).toHaveAttribute(
    'href',
    'https://fotohub.ug/g/tok/',
  )
})
