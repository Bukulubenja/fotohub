import { useState, type FormEvent } from 'react'
import { auth } from '../api/endpoints'
import type { Me } from '../api/types'
import { BRAND_NAME, BRAND_TAGLINE } from '../brand'

export function LoginForm({ onLogin }: { onLogin: (me: Me) => void }) {
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setPending(true)
    setError('')
    try {
      onLogin(await auth.login(String(form.get('email')), String(form.get('password'))))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed.')
    } finally {
      setPending(false)
    }
  }

  return (
    <main className="auth">
      <form className="panel auth__form" onSubmit={submit}>
        <h1 className="wordmark">{BRAND_NAME}</h1>
        <p className="brand__tagline">{BRAND_TAGLINE}</p>
        <p className="muted">Sign in to upload and deliver your galleries.</p>
        <label>
          Email
          <input name="email" type="email" autoComplete="email" required />
        </label>
        <label>
          Password
          <input name="password" type="password" autoComplete="current-password" required />
        </label>
        {error && (
          <p className="notice notice--error" role="alert">
            {error}
          </p>
        )}
        <button className="button" type="submit" disabled={pending}>
          {pending ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </main>
  )
}
