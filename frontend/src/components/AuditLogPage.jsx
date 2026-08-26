import { useEffect, useState } from 'react'
import { API_URL } from '../lib/apiConfig'

function formatTime(iso) {
  try {
    return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  } catch {
    return iso
  }
}

function decisionClass(decision) {
  if (decision === 'DENY') return 'audit-pill audit-deny'
  if (decision === 'WARN') return 'audit-pill audit-warn'
  return 'audit-pill audit-allow'
}

export default function AuditLogPage({ user, onBack }) {
  const [entries, setEntries] = useState(null)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('all')

  useEffect(() => {
    if (user?.role !== 'admin') return
    fetch(`${API_URL}/api/admin/audit-log`, { credentials: 'include' })
      .then((r) => {
        if (!r.ok) throw new Error('Failed to load the audit log.')
        return r.json()
      })
      .then((data) => setEntries(data.entries))
      .catch(() => setError('Could not load the audit log.'))
  }, [user])

  if (user?.role !== 'admin') {
    return (
      <div className="detail-page">
        <header className="detail-page-header">
          <button type="button" className="detail-back" onClick={onBack}>
            ← Back to chat
          </button>
          <div>
            <h1>Audit Log</h1>
            <p>Admins only.</p>
          </div>
        </header>
        <div className="audit-empty">Your account does not have access to this page.</div>
      </div>
    )
  }

  const visible = (entries || []).filter((e) => filter === 'all' || e.source === filter).slice().reverse()

  return (
    <div className="detail-page">
      <header className="detail-page-header">
        <button type="button" className="detail-back" onClick={onBack}>
          ← Back to chat
        </button>
        <div>
          <h1>Audit Log</h1>
          <p>Every deterministic guardrail and capability decision made this server session.</p>
        </div>
        <div className="audit-filter">
          {['all', 'guardrail', 'capability'].map((f) => (
            <button key={f} type="button" className={filter === f ? 'is-active' : ''} onClick={() => setFilter(f)}>
              {f}
            </button>
          ))}
        </div>
      </header>

      <div className="audit-body">
        {error && <div className="audit-empty">{error}</div>}
        {!error && entries === null && <div className="audit-empty">Loading…</div>}
        {!error && entries !== null && visible.length === 0 && (
          <div className="audit-empty">No decisions recorded yet - ask a question from the chat first.</div>
        )}
        {!error && visible.length > 0 && (
          <div className="audit-table-wrap">
            <table className="audit-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Source</th>
                  <th>Stage / Agent</th>
                  <th>Check / Capability</th>
                  <th>Decision</th>
                  <th>Reason</th>
                  <th>Investigation</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((e, i) => (
                  <tr key={i}>
                    <td className="audit-time">{formatTime(e.timestamp)}</td>
                    <td>
                      <span className={`audit-source audit-source-${e.source}`}>{e.source}</span>
                    </td>
                    <td>{e.stage || e.agent}</td>
                    <td className="audit-check">{e.check || e.capability}</td>
                    <td>
                      <span className={decisionClass(e.decision)}>{e.decision}</span>
                    </td>
                    <td className="audit-reason">{e.reason}</td>
                    <td className="audit-time">{e.investigation_id || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
