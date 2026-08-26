// A dedicated, straight top-to-bottom flow for this page's "How it works"
// illustration - deliberately not the same TracePipeline component the live
// sidebar/Detail Flow page use (that one's fan-out/fan-in branching exists
// to show real concurrent execution status; here nothing is actually
// running, so a simple single-column sequence reads more clearly).
const FLOW_STEPS = [
  { type: 'infra', title: 'Client', subtitle: 'ChatPanel.jsx' },
  { type: 'infra', title: 'API', subtitle: 'app/server.py' },
  { type: 'guardrail', title: 'Input Guardrails', subtitle: 'injection · scope · PII · harmful intent' },
  { type: 'guardrail', title: 'RBAC', subtitle: 'role → allowed domains' },
  { type: 'orchestrator', title: 'Supervisor', subtitle: 'plan & delegate — no direct DB/Qdrant access' },
]

// Matches pipelineReducer.js's AGENT_LABELS naming ("Production Agent" etc.)
// so each chip unambiguously names the specific backend agent that runs
// this step, not just its domain.
const SPECIALISTS = [
  { title: 'Production Agent', subtitle: '→ PostgreSQL' },
  { title: 'Maintenance Agent', subtitle: '→ PostgreSQL' },
  { title: 'Quality Agent', subtitle: '→ PostgreSQL' },
  { title: 'Knowledge Agent', subtitle: '→ Qdrant (RAG)' },
]

const FLOW_STEPS_AFTER = [
  { type: 'orchestrator', title: 'Supervisor', subtitle: 'synthesize findings' },
  { type: 'guardrail', title: 'Output Guardrails', subtitle: 'PII · evidence · confidence' },
  { type: 'result', title: 'Result', subtitle: 'final report to the user' },
]

// Same five scenarios app/e2e_report.py exercises end-to-end - picking one
// here starts the app with that exact question prefilled, so what a user
// tries first is a question already known to work against real data.
const EXAMPLE_SCENARIOS = [
  {
    agent: 'Broad',
    title: 'Multi-agent root cause',
    question: 'Why did Line 4 production drop on 2026-08-25?',
  },
  {
    agent: 'Production',
    title: 'Production quantity',
    question: 'How much did Line 4 produce on 2026-08-25?',
  },
  {
    agent: 'Maintenance',
    title: 'Machine downtime',
    question: 'Why did M-104 go down?',
  },
  {
    agent: 'Quality',
    title: 'Rejection rate',
    question: 'Why did Line 4 rejection increase?',
  },
  {
    agent: 'Knowledge',
    title: 'SOP lookup',
    question: 'What does the motor failure SOP recommend?',
  },
]

function FlowStep({ type, title, subtitle }) {
  return (
    <div className={`flow-step type-${type}`}>
      <div className="flow-step-title">{title}</div>
      {subtitle && <div className="flow-step-subtitle">{subtitle}</div>}
    </div>
  )
}

function FlowArrow() {
  return <div className="flow-arrow" aria-hidden="true" />
}

export default function SummaryPage({ user, onStart, onLogout }) {
  return (
    <div className="summary-page">
      <div className="summary-content">
        <header className="summary-header">
          <div className="summary-mark" aria-hidden="true">
            IN
          </div>
          <div className="summary-header-text">
            <h1>Incident Investigator</h1>
            <p>
              A guarded, multi-agent manufacturing incident investigator — real PostgreSQL/Qdrant data, a
              deterministic guardrail + RBAC layer in front of every question, and concurrent specialist delegation
              with full evidence and citations.
            </p>
          </div>
          <div className="summary-header-corner">
            <button type="button" className="summary-start" onClick={() => onStart('')}>
              Start App →
            </button>
            {user && (
              <div className="summary-header-user">
                <span className={`account-badge account-${user.role}`}>
                  {user.username}
                  <em>{user.role}</em>
                </span>
                {onLogout && (
                  <button type="button" className="account-link" onClick={onLogout}>
                    Log out
                  </button>
                )}
              </div>
            )}
          </div>
        </header>

        <section className="summary-section summary-section-centered">
          <h2>How it works</h2>
          <p className="summary-section-sub">
            Every question passes through input guardrails and RBAC before the Supervisor ever runs. The Supervisor
            then delegates to only the specialists a question genuinely needs, running them concurrently, before
            synthesizing one final, evidence-backed report.
          </p>
          <div className="flow-steps">
            {FLOW_STEPS.flatMap((step, i) => [
              <FlowStep key={`step-${i}`} {...step} />,
              <FlowArrow key={`arrow-${i}`} />,
            ])}

            <div className="flow-step flow-step-group">
              <div className="flow-step-title">
                Specialists <span className="flow-step-tag">concurrent</span>
              </div>
              <div className="flow-step-group-grid">
                {SPECIALISTS.map((s) => (
                  <div key={s.title} className="flow-chip">
                    <span className="flow-chip-title">{s.title}</span>
                    <span className="flow-chip-subtitle">{s.subtitle}</span>
                  </div>
                ))}
              </div>
            </div>

            {FLOW_STEPS_AFTER.flatMap((step, i) => [
              <FlowArrow key={`arrow-after-${i}`} />,
              <FlowStep key={`step-after-${i}`} {...step} />,
            ])}
          </div>
        </section>

        <section className="summary-section">
          <h2>Example scenarios</h2>
          <p className="summary-section-sub">Pick one to start with it prefilled, or start with a blank chat below.</p>
          <div className="summary-scenarios">
            {EXAMPLE_SCENARIOS.map((s) => (
              <button key={s.title} type="button" className="summary-scenario-card" onClick={() => onStart(s.question)}>
                <span className="summary-scenario-agent">{s.agent}</span>
                <span className="summary-scenario-title">{s.title}</span>
                <span className="summary-scenario-question">&ldquo;{s.question}&rdquo;</span>
              </button>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}
