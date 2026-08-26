import { useState } from 'react'
import { API_URL } from '../lib/apiConfig'

export default function LoginPage({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function submit(e) {
    e.preventDefault()
    if (!username.trim() || !password || submitting) return
    setSubmitting(true)
    setError('')
    try {
      const res = await fetch(`${API_URL}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ username: username.trim(), password }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setError(body.detail || 'Invalid username or password.')
        return
      }
      const data = await res.json()
      onLogin(data)
    } catch {
      setError('Could not reach the server. Is the backend running?')
    } finally {
      setSubmitting(false)
    }
  }

  function fillDemo(role) {
    setUsername(role)
    setPassword(role === 'admin' ? 'admin123' : 'user123')
    setError('')
  }

  return (
    <div className="login-shell">
      <form className="login-card" onSubmit={submit}>
        <div className="login-mark" aria-hidden="true">
          IN
        </div>
        <h1>Incident Investigator</h1>
        <p className="login-subtitle">Sign in to run a manufacturing incident investigation.</p>

        {error && <div className="login-error">{error}</div>}

        <label className="login-field">
          <span>Username</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            disabled={submitting}
          />
        </label>

        <label className="login-field">
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            disabled={submitting}
          />
        </label>

        <button type="submit" className="login-submit" disabled={submitting || !username.trim() || !password}>
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>

        <div className="login-demo">
          <span>Demo accounts</span>
          <div className="login-demo-buttons">
            <button type="button" onClick={() => fillDemo('admin')} disabled={submitting}>
              admin
            </button>
            <button type="button" onClick={() => fillDemo('user')} disabled={submitting}>
              user
            </button>
          </div>
        </div>
      </form>
    </div>
  )
}
