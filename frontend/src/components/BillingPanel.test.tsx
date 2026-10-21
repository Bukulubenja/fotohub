import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Mock } from 'vitest'
import type { BillingApi } from '../api/endpoints'
import type { Payment, Plan, Subscription } from '../api/types'
import { BillingPanel } from './BillingPanel'

const starter: Plan = {
  code: 'starter',
  name: 'Starter',
  price_ugx: 30_000,
  period_days: 30,
  storage_quota_bytes: 25 * 1024 ** 3,
}

function subscription(overrides: Partial<Subscription> = {}): Subscription {
  return {
    plan: { ...starter, code: 'trial', name: 'Free trial', price_ugx: 0 },
    paid_until: '2026-11-04T12:00:00Z',
    state: 'active',
    can_upload: true,
    galleries_live: true,
    days_left: 13,
    ...overrides,
  }
}

function payment(status: Payment['status'], failure_reason = ''): Payment {
  return { id: 'p1', plan: 'starter', periods: 3, amount_ugx: 90_000, status, failure_reason }
}

type FakeBillingApi = { [K in keyof BillingApi]: Mock<BillingApi[K]> }

function fakeApi(sub: Subscription, outcomes: Payment[] = []): FakeBillingApi {
  const paid = subscription({ plan: starter, paid_until: '2027-02-04T12:00:00Z', days_left: 105 })
  let settled = false
  return {
    plans: vi.fn(async () => [starter]),
    subscription: vi.fn(async () => (settled ? paid : sub)),
    startPayment: vi.fn(async () => ({
      payment: payment('pending'),
      next: { action: 'prompt' as const, message: 'Approve UGX 90,000 on 256772123456.', redirect_url: '' },
    })),
    payment: vi.fn(async () => {
      const next = outcomes.shift() ?? payment('pending')
      if (next.status === 'succeeded') settled = true
      return next
    }),
  }
}

test('trial shows the days left and offers a plan', async () => {
  render(<BillingPanel api={fakeApi(subscription())} />)

  expect(await screen.findByText('Free trial: 13 days left.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Choose a plan' })).toBeInTheDocument()
})

test('grace period explains that uploads are paused', async () => {
  const onChange = vi.fn()
  render(
    <BillingPanel
      api={fakeApi(subscription({ plan: starter, state: 'grace', can_upload: false, days_left: 0 }))}
      onChange={onChange}
    />,
  )

  expect(await screen.findByRole('alert')).toHaveTextContent('uploads are paused')
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ can_upload: false }))
})

test('hidden galleries are explained without alarming about the photos', async () => {
  render(
    <BillingPanel
      api={fakeApi(subscription({ plan: starter, state: 'lapsed', can_upload: false, galleries_live: false }))}
    />,
  )

  expect(await screen.findByRole('alert')).toHaveTextContent('Your photos are safe')
})

test('renewing prompts the phone, waits, then shows the new end date', async () => {
  const user = userEvent.setup()
  const api = fakeApi(subscription(), [payment('pending'), payment('succeeded')])
  const onChange = vi.fn()
  render(<BillingPanel api={api} onChange={onChange} pollMs={1} />)

  await user.click(await screen.findByRole('button', { name: 'Choose a plan' }))
  await user.selectOptions(screen.getByLabelText('Pay for'), '3')
  await user.type(screen.getByLabelText('Mobile money number'), '0772123456')
  await user.click(screen.getByRole('button', { name: 'Pay UGX 90,000' }))

  expect(api.startPayment).toHaveBeenCalledWith('starter', 3, '0772123456')
  expect(await screen.findByText(/You're paid up until 4 February 2027/)).toBeInTheDocument()
  expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ days_left: 105 }))
})

test('a declined payment shows the reason and lets them try again', async () => {
  const user = userEvent.setup()
  const api = fakeApi(subscription(), [payment('failed', 'Not enough balance.')])
  render(<BillingPanel api={api} pollMs={1} />)

  await user.click(await screen.findByRole('button', { name: 'Choose a plan' }))
  await user.type(screen.getByLabelText('Mobile money number'), '0772123456')
  await user.click(screen.getByRole('button', { name: /^Pay / }))

  expect(await screen.findByText('Not enough balance.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /^Pay / })).toBeInTheDocument()
})

test('a rejected start (bad number) is shown on the form', async () => {
  const user = userEvent.setup()
  const api = fakeApi(subscription())
  api.startPayment.mockRejectedValueOnce(new Error('Enter an MTN or Airtel number, for example 0772 123 456.'))
  render(<BillingPanel api={api} />)

  await user.click(await screen.findByRole('button', { name: 'Choose a plan' }))
  await user.type(screen.getByLabelText('Mobile money number'), '123')
  await user.click(screen.getByRole('button', { name: /^Pay / }))

  expect(await screen.findByText(/Enter an MTN or Airtel number/)).toBeInTheDocument()
})
