import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { UploadConfig } from '../api/types'
import type { UploadItem, UploadQueue, UploadSummary } from '../upload/queue'
import { screenFiles } from '../upload/screenFiles'
import { UploadPanel } from './UploadPanel'

const config: UploadConfig = {
  allowed_content_types: ['image/jpeg', 'image/png', 'image/heic'],
  max_file_bytes: 1000,
  max_parts_per_sign_request: 100,
}

function fakeQueue() {
  return {
    add: vi.fn(),
    retryFailed: vi.fn(),
    cancel: vi.fn(),
    clearFinished: vi.fn(),
  } as unknown as UploadQueue & Record<'add' | 'retryFailed' | 'cancel', ReturnType<typeof vi.fn>>
}

const emptySummary: UploadSummary = {
  total: 0, done: 0, failed: 0, active: 0, uploadedBytes: 0, totalBytes: 0,
}

function item(name: string, status: UploadItem['status'], error?: string): UploadItem {
  return {
    id: name, file: new File(['x'], name), contentType: 'image/jpeg', status, uploadedBytes: 0, error,
  }
}

test('screenFiles accepts photos and explains every rejection', () => {
  const files = [
    new File(['x'], 'ok.jpg', { type: 'image/jpeg' }),
    new File(['x'], 'IMG_1.HEIC', { type: '' }),
    new File(['x'], 'doc.pdf', { type: 'application/pdf' }),
    new File([new Uint8Array(2000)], 'huge.jpg', { type: 'image/jpeg' }),
    new File([], 'empty.png', { type: 'image/png' }),
  ]

  const { accepted, rejected } = screenFiles(files, config)

  expect(accepted.map((a) => [a.file.name, a.contentType])).toEqual([
    ['ok.jpg', 'image/jpeg'],
    ['IMG_1.HEIC', 'image/heic'],
  ])
  expect(rejected.map((r) => r.name)).toEqual(['doc.pdf', 'huge.jpg', 'empty.png'])
})

test('chosen files go to the queue and skipped ones are listed', async () => {
  const queue = fakeQueue()
  render(<UploadPanel queue={queue} items={[]} summary={emptySummary} config={config} />)

  await userEvent.upload(screen.getByTestId('file-input'), [
    new File(['x'], 'a.jpg', { type: 'image/jpeg' }),
    new File(['x'], 'b.pdf', { type: 'application/pdf' }),
  ], { applyAccept: false })

  expect(queue.add).toHaveBeenCalledWith([expect.objectContaining({ contentType: 'image/jpeg' })])
  expect(screen.getByRole('alert')).toHaveTextContent('1 file skipped')
  expect(screen.getByRole('alert')).toHaveTextContent('b.pdf')
})

test('shows how many failed and retries just those', async () => {
  const queue = fakeQueue()
  const items = [item('a.jpg', 'done'), item('b.jpg', 'failed', 'Connection lost while uploading.')]
  const summary = { ...emptySummary, total: 400, done: 388, failed: 12 }
  render(<UploadPanel queue={queue} items={items} summary={summary} config={config} />)

  expect(screen.getByRole('status')).toHaveTextContent('12 of 400 photos failed')
  expect(screen.getByText(/Connection lost while uploading/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Retry those 12' }))

  expect(queue.retryFailed).toHaveBeenCalledOnce()
})

test('finished files collapse out of the list; problems are listed first', () => {
  const items = [item('done.jpg', 'done'), item('up.jpg', 'uploading'), item('bad.jpg', 'failed')]
  render(
    <UploadPanel
      queue={fakeQueue()}
      items={items}
      summary={{ ...emptySummary, total: 3, done: 1, failed: 1, active: 1 }}
      config={config}
    />,
  )

  const names = screen.getAllByRole('listitem').map((li) => li.querySelector('.file__name')?.textContent)
  expect(names).toEqual(['bad.jpg', 'up.jpg'])
})
