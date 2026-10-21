import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { billing as billingApi, type BillingApi } from '../api/endpoints'
import type { Plan, Subscription } from '../api/types'
import { formatBytes, formatDate, formatUgx, plural } from '../format'

const PERIOD_CHOICES = [1, 3, 6, 12]
const POLL_MS = 3_000
const POLL_LIMIT = 100 // ~5 minutes of waiting for the photographer to approve on the phone

type Renewal =
  | { step: 'idle' }
  | { step: 'waiting'; message: string }
  | { step: 'done'; paidUntil: string }
  | { step: 'failed'; message: string }

function standingMessage(sub: Subscription): { tone: 'info' | 'warn' | 'alert'; text: string } {
  const isTrial = sub.plan.code === 'trial'
  switch (sub.state) {
    case 'active':
      return isTrial
        ? { tone: 'info', text: `Free trial: ${plural(sub.days_left, 'day')} left.` }
        : { tone: 'info', text: `${sub.plan.name} plan, paid until ${formatDate(sub.paid_until)}.` }
    case 'due':
      return {
        tone: 'warn',
        text: `Your ${isTrial ? 'trial' : 'plan'} ends in ${plural(sub.days_left, 'day')}. Renew to keep uploading.`,
      }
    case 'grace':
      return {
        tone: 'alert',
        text: 'Your plan has ended, so uploads are paused. Your galleries stay online for now.',
      }
    case 'lapsed':
      return {
        tone: 'alert',
        text: sub.galleries_live
          ? 'Your plan has lapsed. Galleries are still online, but will be hidden from clients soon.'
          : 'Your plan has lapsed and your galleries are hidden from clients. Your photos are safe: renew to bring them back.',
      }
  }
}

/** Subscription status, and renewal by mobile money (spec FR-26, FR-29). */
export function BillingPanel({
  onChange,
  api = billingApi,
  pollMs = POLL_MS,
}: {
  onChange?: (subscription: Subscription) => void
  api?: BillingApi
  pollMs?: number
}) {
  const [sub, setSub] = useState<Subscription | null>(null)
  const [plans, setPlans] = useState<Plan[]>([])
  const [open, setOpen] = useState(false)
  const [plan, setPlan] = useState('')
  const [periods, setPeriods] = useState(1)
  const [phone, setPhone] = useState('')
  const [renewal, setRenewal] = useState<Renewal>({ step: 'idle' })
  const [error, setError] = useState('')
  const cancelled = useRef(false)

  const refresh = useCallback(async () => {
    const latest = await api.subscription()
    setSub(latest)
    onChange?.(latest)
    return latest
  }, [api, onChange])

  useEffect(() => {
    cancelled.current = false
    api.subscription().then(
      (latest) => {
        setSub(latest)
        onChange?.(latest)
      },
      () => setError("Couldn't load your plan."),
    )
    api.plans().then((list) => {
      setPlans(list)
      setPlan((current) => current || list[0]?.code || '')
    }, () => undefined)
    return () => {
      cancelled.current = true
    }
  }, [api, onChange])

  const waitFor = async (paymentId: string) => {
    for (let i = 0; i < POLL_LIMIT && !cancelled.current; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, pollMs))
      const payment = await api.payment(paymentId).catch(() => null)
      if (payment?.status === 'succeeded') {
        const latest = await refresh()
        setRenewal({ step: 'done', paidUntil: latest.paid_until })
        setOpen(false)
        return
      }
      if (payment?.status === 'failed') {
        setRenewal({ step: 'failed', message: payment.failure_reason || 'The payment didn\'t go through.' })
        return
      }
    }
    if (!cancelled.current) {
      setRenewal({
        step: 'failed',
        message: "We haven't heard back about the payment yet. If money left your account, it will show here soon.",
      })
    }
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    try {
      const started = await api.startPayment(plan, periods, phone)
      if (started.next.action === 'redirect' && started.next.redirect_url) {
        window.location.assign(started.next.redirect_url)
        return
      }
      setRenewal({ step: 'waiting', message: started.next.message })
      await waitFor(started.payment.id)
    } catch (err) {
      setRenewal({ step: 'idle' })
      setError(err instanceof Error ? err.message : 'The payment could not start.')
    }
  }

  if (!sub) return error ? <p className="notice notice--error" role="alert">{error}</p> : null

  const standing = standingMessage(sub)
  const chosen = plans.find((p) => p.code === plan)

  return (
    <section className={`panel billing billing--${standing.tone}`} aria-labelledby="billing-heading">
      <h2 id="billing-heading" className="visually-hidden">Your plan</h2>
      <p role={standing.tone === 'info' ? undefined : 'alert'}>{standing.text}</p>
      {renewal.step === 'done' && (
        <p className="notice" role="status">Payment received. You're paid up until {formatDate(renewal.paidUntil)}.</p>
      )}
      {renewal.step === 'waiting' && (
        <p className="notice" role="status">{renewal.message} Waiting for confirmation…</p>
      )}
      {renewal.step === 'failed' && <p className="notice notice--error" role="alert">{renewal.message}</p>}
      {!open && renewal.step !== 'waiting' && (
        <button type="button" className="button button--small" onClick={() => setOpen(true)}>
          {sub.plan.code === 'trial' ? 'Choose a plan' : 'Renew'}
        </button>
      )}
      {open && renewal.step !== 'waiting' && (
        <form className="renew" onSubmit={submit}>
          <label>
            Plan
            <select value={plan} onChange={(event) => setPlan(event.target.value)}>
              {plans.map((p) => (
                <option key={p.code} value={p.code}>
                  {p.name}: {formatUgx(p.price_ugx)} / {p.period_days} days, {formatBytes(p.storage_quota_bytes)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Pay for
            <select value={periods} onChange={(event) => setPeriods(Number(event.target.value))}>
              {PERIOD_CHOICES.map((n) => (
                <option key={n} value={n}>
                  {plural(n, 'month')}
                </option>
              ))}
            </select>
          </label>
          <label>
            Mobile money number
            <input
              type="tel"
              inputMode="tel"
              autoComplete="tel"
              placeholder="0772 123 456"
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
              required
            />
          </label>
          {error && <p className="notice notice--error" role="alert">{error}</p>}
          <div className="renew-actions">
            <button type="submit" className="button">
              Pay {chosen ? formatUgx(chosen.price_ugx * periods) : ''}
            </button>
            <button type="button" className="link" onClick={() => setOpen(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </section>
  )
}
