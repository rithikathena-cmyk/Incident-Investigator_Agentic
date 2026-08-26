// Fixed-geometry SVG connectors between pipeline tiers. Positions are
// computed constants (not measured DOM), so - unlike the earlier React
// Flow camera - there is nothing to race against node-mount timing; the
// diagram simply can't misalign itself.

export const AGENT_X = [120, 375, 625, 880]
const NEUTRAL = '#b0aea3'

// 'active' and 'done' are both rendered in the same green family so the
// whole route the current query is taking - from entry gate through
// whichever specialist is running right now - reads as one continuous
// green path, not a color change partway through. 'active' gets a
// brighter green (plus the dashed/pulse animation below) so the node
// actually doing work is still distinguishable from the settled trail
// behind it.
export function statusColor(status) {
  if (status === 'blocked' || status === 'error') return '#c53434'
  if (status === 'done') return '#1f7a4d'
  if (status === 'active') return '#2f9e5c'
  return NEUTRAL
}

function lineClass(status) {
  return status === 'active' ? 'connector-line connector-animated' : 'connector-line'
}

export function ConnectorStem({ status }) {
  const color = statusColor(status)
  return (
    <div className="connector-stem-wrap">
      <div className={status === 'active' ? 'connector-stem connector-animated-v' : 'connector-stem'} style={{ background: color }} />
      <div className="connector-stem-arrow" style={{ borderTopColor: color }} />
    </div>
  )
}

export function Merge2({ statuses }) {
  const [c1, c2] = statuses.map(statusColor)
  return (
    <svg className="connector-svg" viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <marker id="arrowMerge2" markerWidth="9" markerHeight="9" refX="4.5" refY="4.5" orient="auto">
          <path d="M0,0 L9,4.5 L0,9 Z" fill={NEUTRAL} />
        </marker>
      </defs>
      <line x1="250" y1="0" x2="250" y2="55" stroke={c1} strokeWidth="2.5" className={lineClass(statuses[0])} />
      <line x1="750" y1="0" x2="750" y2="55" stroke={c2} strokeWidth="2.5" className={lineClass(statuses[1])} />
      <line x1="250" y1="55" x2="750" y2="55" stroke={NEUTRAL} strokeWidth="2" />
      <line x1="500" y1="55" x2="500" y2="96" stroke={NEUTRAL} strokeWidth="2.5" markerEnd="url(#arrowMerge2)" />
      <circle cx="250" cy="55" r="4" fill={c1} />
      <circle cx="750" cy="55" r="4" fill={c2} />
      <circle cx="500" cy="55" r="4" fill={NEUTRAL} />
    </svg>
  )
}

export function FanOut4({ statuses }) {
  return (
    <svg className="connector-svg connector-svg-fan" viewBox="0 0 1000 64" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <marker id="arrowFanOut" markerWidth="9" markerHeight="9" refX="4.5" refY="4.5" orient="auto">
          <path d="M0,0 L9,4.5 L0,9 Z" fill={NEUTRAL} />
        </marker>
      </defs>
      <line x1="500" y1="0" x2="500" y2="22" stroke={NEUTRAL} strokeWidth="2.5" />
      <line x1="120" y1="22" x2="880" y2="22" stroke={NEUTRAL} strokeWidth="2" />
      {AGENT_X.map((x, i) => (
        <line
          key={x}
          x1={x} y1="22" x2={x} y2="58"
          stroke={statusColor(statuses[i])}
          strokeWidth={statuses[i] === 'pending' ? 1.5 : 2.5}
          className={lineClass(statuses[i])}
          markerEnd="url(#arrowFanOut)"
        />
      ))}
      <circle cx="500" cy="22" r="3.5" fill={NEUTRAL} />
      {AGENT_X.map((x) => (
        <circle key={x} cx={x} cy="22" r="3.5" fill={NEUTRAL} />
      ))}
    </svg>
  )
}

export function FanIn4({ statuses }) {
  return (
    <svg className="connector-svg connector-svg-fan" viewBox="0 0 1000 64" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <marker id="arrowFanIn" markerWidth="9" markerHeight="9" refX="4.5" refY="4.5" orient="auto">
          <path d="M0,0 L9,4.5 L0,9 Z" fill={NEUTRAL} />
        </marker>
      </defs>
      {AGENT_X.map((x, i) => (
        <line
          key={x}
          x1={x} y1="6" x2={x} y2="42"
          stroke={statusColor(statuses[i])}
          strokeWidth={statuses[i] === 'pending' ? 1.5 : 2.5}
          className={lineClass(statuses[i])}
        />
      ))}
      <line x1="120" y1="42" x2="880" y2="42" stroke={NEUTRAL} strokeWidth="2" />
      <line x1="500" y1="42" x2="500" y2="60" stroke={NEUTRAL} strokeWidth="2.5" markerEnd="url(#arrowFanIn)" />
      {AGENT_X.map((x) => (
        <circle key={x} cx={x} cy="42" r="3.5" fill={NEUTRAL} />
      ))}
      <circle cx="500" cy="42" r="3.5" fill={NEUTRAL} />
    </svg>
  )
}
