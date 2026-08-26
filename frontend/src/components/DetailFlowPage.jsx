import { useState } from 'react'
import DetailLog from './DetailLog'
import TracePipeline from './TracePipeline'

function formatWhen(date) {
  if (!date) return ''
  return date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
}

function entryStatus(entry, isCurrent, running) {
  if (entry.pipeline.done) {
    return entry.pipeline.done.allowed ? { label: 'Complete', tone: 'status-done' } : { label: 'Blocked', tone: 'status-blocked' }
  }
  if (isCurrent && running) return { label: 'Running…', tone: 'status-active' }
  return { label: 'Incomplete', tone: 'status-blocked' }
}

export default function DetailFlowPage({ history, running, onBack }) {
  const [selectedId, setSelectedId] = useState(null)
  const latestId = history.length > 0 ? history[history.length - 1].id : null
  const selected = history.find((h) => h.id === selectedId) || history.find((h) => h.id === latestId) || null
  const ordered = [...history].reverse()

  return (
    <div className="detail-page">
      <header className="detail-page-header">
        <button type="button" className="detail-back" onClick={onBack}>
          ← Back to chat
        </button>
        <div>
          <h1>Detail Flow</h1>
          <p>Full investigation trace diagrams, browsable by question.</p>
        </div>
      </header>

      <div className="detail-page-body">
        <nav className="detail-history">
          <h2>Question History</h2>
          {history.length === 0 && (
            <p className="detail-history-empty">No investigations yet. Ask a question from the chat first.</p>
          )}
          <ul>
            {ordered.map((entry) => {
              const status = entryStatus(entry, entry.id === latestId, running)
              const isSelected = selected?.id === entry.id
              return (
                <li key={entry.id}>
                  <button
                    type="button"
                    className={`detail-history-item ${isSelected ? 'is-selected' : ''}`}
                    onClick={() => setSelectedId(entry.id)}
                  >
                    <span className={`detail-history-status ${status.tone}`}>{status.label}</span>
                    <span className="detail-history-question">{entry.question}</span>
                    <span className="detail-history-time">{formatWhen(entry.startedAt)}</span>
                  </button>
                </li>
              )
            })}
          </ul>
        </nav>

        <div className="detail-page-main">
          {selected ? (
            <>
              <div className="detail-diagram-scroll">
                <TracePipeline pipeline={selected.pipeline} question={selected.question} running={running && selected.id === latestId} />
              </div>
              <DetailLog log={selected.pipeline.log} />
            </>
          ) : (
            <div className="detail-page-empty">Select a question from the history to see its flow diagram.</div>
          )}
        </div>
      </div>
    </div>
  )
}
