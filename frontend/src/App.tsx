import { useEffect, useState } from 'react'
import { auth } from './api/endpoints'
import type { Me } from './api/types'
import { Dashboard } from './components/Dashboard'
import { LoginForm } from './components/LoginForm'

type Session = { state: 'loading' } | { state: 'anonymous' } | { state: 'signed-in'; me: Me }

export default function App() {
  const [session, setSession] = useState<Session>({ state: 'loading' })

  useEffect(() => {
    auth
      .me()
      .then((me) => setSession({ state: 'signed-in', me }))
      // 401/403 means "not signed in"; an outage also lands on the form, which shows errors.
      .catch(() => setSession({ state: 'anonymous' }))
  }, [])

  if (session.state === 'loading') return <p className="muted center">Loading…</p>
  if (session.state === 'anonymous')
    return <LoginForm onLogin={(me) => setSession({ state: 'signed-in', me })} />
  return <Dashboard me={session.me} onLogout={() => setSession({ state: 'anonymous' })} />
}
