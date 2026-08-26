import { useEffect, useRef, useState } from 'react'
import MessageBubble from './MessageBubble'
import RoleSelector from './RoleSelector'

export default function ChatPanel({ messages, running, roles, role, onRoleChange, onAsk, connected, user, onLogout, onOpenAudit, initialInput }) {
  // Prefill from a scenario card on SummaryPage (see App.jsx's handleStart) -
  // lazy useState initializer, so this only ever seeds on mount and doesn't
  // fight the user's own typing afterward.
  const [input, setInput] = useState(() => initialInput || '')
  const listRef = useRef(null)

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  function submit(e) {
    e.preventDefault()
    const question = input.trim()
    if (!question || running || !connected) return
    onAsk(question, role)
    setInput('')
  }

  return (
    <div className="chat-panel">
      <div className="chat-header">
        <div className="chat-header-title">
          <h1>Incident Investigator</h1>
          <span className={`connection-dot ${connected ? 'connected' : 'disconnected'}`}>
            {connected ? 'connected' : 'reconnecting…'}
          </span>
        </div>
        <div className="chat-header-row">
          {roles.length > 0 && <RoleSelector roles={roles} role={role} onChange={onRoleChange} />}
          {user && (
            <div className="account-menu">
              <span className={`account-badge account-${user.role}`}>
                {user.username}
                <em>{user.role}</em>
              </span>
              {user.role === 'admin' && (
                <button type="button" className="account-link" onClick={onOpenAudit}>
                  Audit log
                </button>
              )}
              <button type="button" className="account-link" onClick={onLogout}>
                Log out
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="message-list" ref={listRef}>
        {messages.length === 0 && (
          <div className="empty-state">
            Ask something like <em>&ldquo;Why did Line 4 production drop yesterday, and was machine downtime or quality
            degradation the primary cause?&rdquo;</em>
          </div>
        )}
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}
      </div>

      <form className="chat-input" onSubmit={submit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about a production, maintenance, or quality incident…"
          disabled={running || !connected}
        />
        <button type="submit" disabled={running || !connected || !input.trim()}>
          {running ? 'Investigating…' : 'Ask'}
        </button>
      </form>
    </div>
  )
}
