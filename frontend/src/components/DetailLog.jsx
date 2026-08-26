import { useEffect, useRef } from 'react'

const LEVEL_CLASS = {
  info: 'log-info',
  success: 'log-success',
  warn: 'log-warn',
  error: 'log-error',
}

function formatTime(date) {
  return date.toLocaleTimeString([], { hour12: false })
}

export default function DetailLog({ log }) {
  const listRef = useRef(null)

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [log])

  return (
    <div className="detail-log">
      <h2>Detail Log</h2>
      <div className="detail-log-list" ref={listRef}>
        {log.length === 0 && <div className="detail-log-empty">Execution steps will appear here as an investigation runs.</div>}
        {log.map((entry) => (
          <div key={entry.id} className={`detail-log-entry ${LEVEL_CLASS[entry.level] || 'log-info'}`}>
            <div className="detail-log-meta">
              <span className="detail-log-step">Step {entry.step ?? '–'}</span>
              <span className="detail-log-time">{formatTime(entry.time)}</span>
            </div>
            <p>{entry.text}</p>
          </div>
        ))}
      </div>
    </div>
  )
}
