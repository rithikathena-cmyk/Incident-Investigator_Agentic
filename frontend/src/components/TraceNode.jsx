import { statusColor } from './BusConnector'

const STATUS_LABEL = {
  idle: 'IDLE',
  pending: 'PENDING',
  active: 'RUNNING',
  done: 'DONE',
  blocked: 'DENIED',
  error: 'ERROR',
}

function statusDot(status) {
  if (status === 'done') return '✓'
  if (status === 'blocked' || status === 'error') return '✕'
  if (status === 'active') return '●'
  return ''
}

function checkClass(check) {
  if (check.allowed === false) return 'check-fail'
  if (check.decision === 'WARN') return 'check-warn'
  return 'check-pass'
}

function summarizeTools(toolCalls) {
  const order = []
  const byName = {}
  toolCalls.forEach((c) => {
    if (!byName[c.tool]) {
      byName[c.tool] = { name: c.tool, total: 0, ok: 0, err: 0, active: 0 }
      order.push(c.tool)
    }
    const t = byName[c.tool]
    t.total += 1
    if (c.status === 'done') t.ok += 1
    else if (c.status === 'error') t.err += 1
    else t.active += 1
  })
  return order.map((n) => byName[n])
}

function formatTime(time) {
  if (!time) return null
  return time.toLocaleTimeString([], { hour12: false })
}

export default function TraceNode({ type, num, title, subtitle, status, statusLabel, checks, toolCalls, dataStore, finding, time, compact = false }) {
  const color = statusColor(status)
  const label = statusLabel || STATUS_LABEL[status] || status
  const tools = toolCalls ? summarizeTools(toolCalls) : null
  const stamp = status !== 'pending' && status !== 'idle' ? formatTime(time) : null

  return (
    <div
      className={`trace-node type-${type} status-${status}${compact ? ' trace-node-compact' : ''}`}
      style={{ borderColor: color }}
    >
      <div className="trace-node-head">
        {num != null && (
          <span className="trace-num" style={{ background: `${color}26`, color }}>
            {num}
          </span>
        )}
        <span className="trace-title">{title}</span>
      </div>

      {subtitle && !compact && <div className="trace-subtitle">{subtitle}</div>}

      {!compact && checks && checks.length > 0 && (
        <ul className="trace-checks">
          {checks.map((c, i) => (
            <li key={i} className={checkClass(c)}>
              {c.check || 'rbac'}: {c.decision}
            </li>
          ))}
        </ul>
      )}

      {!compact && tools && tools.length > 0 && (
        <div className="trace-tools">
          <span className="trace-tools-label">Tools</span>
          <div className="trace-tools-list">
            {tools.map((t) => (
              <span
                key={t.name}
                className={`tool-chip ${t.active > 0 ? 'chip-active' : t.err > 0 && t.ok === 0 ? 'chip-error' : 'chip-done'}`}
              >
                {t.name}
                {t.total > 1 ? ` ×${t.total}` : ''}
                {t.err > 0 && <em>{t.err}✗</em>}
              </span>
            ))}
          </div>
        </div>
      )}

      {compact && tools && tools.length > 0 && (
        <div className="trace-tools-compact">
          {tools.reduce((sum, t) => sum + t.total, 0)} tool call{tools.reduce((sum, t) => sum + t.total, 0) === 1 ? '' : 's'}
        </div>
      )}

      {!compact && dataStore && <div className="trace-datastore">→ {dataStore}</div>}

      {finding && (
        <div className="trace-finding">
          <div className="trace-finding-bar">
            <div style={{ width: `${Math.round(finding.confidence * 100)}%` }} />
          </div>
          <span>{Math.round(finding.confidence * 100)}% confidence</span>
        </div>
      )}

      <div className="trace-status-row">
        <span className="trace-status" style={{ color }}>
          {label} {statusDot(status)}
        </span>
        {stamp && <span className="trace-time">{stamp}</span>}
      </div>
    </div>
  )
}
