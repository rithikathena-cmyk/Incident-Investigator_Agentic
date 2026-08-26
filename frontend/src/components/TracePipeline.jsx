import { ConnectorStem, FanIn4, FanOut4, Merge2 } from './BusConnector'
import TraceNode from './TraceNode'

const AGENT_ORDER_FIXED = ['production', 'maintenance', 'quality', 'knowledge']
const AGENT_META = {
  production: { title: 'Production', num: 3, dataStore: 'PostgreSQL' },
  maintenance: { title: 'Maintenance', num: 4, dataStore: 'PostgreSQL' },
  quality: { title: 'Quality', num: 5, dataStore: 'PostgreSQL' },
  knowledge: { title: 'Knowledge', num: 6, dataStore: 'Qdrant (RAG)' },
}

function Tier({ n, label, children }) {
  return (
    <section className="tier">
      <div className="tier-label">
        Tier {n} — {label}
      </div>
      {children}
    </section>
  )
}

function apiStatus(running, pipeline) {
  if (pipeline.inputGuardrail.status !== 'pending') return 'done'
  return running ? 'active' : 'idle'
}

export default function TracePipeline({ pipeline, question, running, compact = false }) {
  const supervisorPlanStatus = pipeline.rbac.status !== 'done' ? 'pending' : pipeline.supervisor.status
  const agentsForFan = AGENT_ORDER_FIXED.map((key) => pipeline.agents[key]?.status || 'pending')
  const allAgentsDone = pipeline.agentOrder.length > 0 && pipeline.agentOrder.every((k) => pipeline.agents[k].status === 'done')
  const synthesizeStatus = pipeline.agentOrder.length === 0 ? 'pending' : allAgentsDone ? (pipeline.done ? 'done' : 'active') : 'pending'
  const resultStatus = pipeline.done ? (pipeline.done.allowed ? 'done' : 'blocked') : 'pending'

  return (
    <div className={`trace-pipeline${compact ? ' trace-pipeline-compact' : ''}`}>
      <Tier n={1} label="Client & API">
        <div className="tier-row">
          <TraceNode compact={compact} type="infra" title="Client" subtitle="ChatPanel.jsx" status={question ? 'done' : 'idle'} statusLabel={question ? 'SENT' : 'IDLE'} />
          <div className="tier-inline-arrow" />
          <TraceNode compact={compact} type="infra" title="API" subtitle="app/server.py" status={apiStatus(running, pipeline)} />
        </div>
      </Tier>

      <ConnectorStem status={pipeline.inputGuardrail.status === 'pending' ? (running ? 'active' : 'pending') : 'done'} />

      <Tier n={2} label="Entry Gate">
        <div className="tier-row">
          <TraceNode
            compact={compact}
            type="guardrail"
            num={0}
            title="Input Guardrails"
            subtitle="injection · scope · PII · harmful intent"
            status={pipeline.inputGuardrail.status}
            checks={pipeline.inputGuardrail.decisions}
            time={pipeline.inputGuardrail.time}
          />
          <div className="tier-inline-arrow" />
          <TraceNode
            compact={compact}
            type="guardrail"
            num={1}
            title="RBAC"
            subtitle={pipeline.rbac.domains?.length ? `domains: ${pipeline.rbac.domains.join(', ')}` : 'role → allowed domains'}
            status={pipeline.rbac.status}
            checks={pipeline.rbac.decision ? [pipeline.rbac.decision] : []}
            time={pipeline.rbac.time}
          />
        </div>
      </Tier>

      <Merge2 statuses={[pipeline.inputGuardrail.status, pipeline.rbac.status]} />

      <Tier n={3} label="Supervisor">
        <div className="tier-row">
          <TraceNode
            compact={compact}
            type="orchestrator"
            num={2}
            title="Supervisor"
            subtitle="plan & delegate — no direct DB/Qdrant access"
            status={supervisorPlanStatus}
            time={pipeline.supervisor.time}
          />
        </div>
      </Tier>

      <FanOut4 statuses={agentsForFan} />

      <Tier n={4} label="Specialist Agents">
        <div className="tier-columns">
          {AGENT_ORDER_FIXED.map((key) => {
            const meta = AGENT_META[key]
            const agent = pipeline.agents[key]
            return (
              <TraceNode
                key={key}
                compact={compact}
                type="agent"
                num={meta.num}
                title={meta.title}
                status={agent?.status || 'pending'}
                toolCalls={agent?.toolCalls}
                dataStore={meta.dataStore}
                finding={agent?.finding}
                time={agent?.status === 'done' ? agent?.time : agent?.startedAt}
              />
            )
          })}
        </div>
      </Tier>

      <FanIn4 statuses={agentsForFan} />

      <Tier n={5} label="Synthesis & Exit">
        <div className="tier-row tier-row-exit">
          <TraceNode
            compact={compact}
            type="orchestrator"
            title="Supervisor"
            subtitle="synthesize findings"
            status={synthesizeStatus}
            time={pipeline.synthesis.time}
          />
          <div className="tier-inline-arrow" />
          <TraceNode
            compact={compact}
            type="guardrail"
            num={7}
            title="Output Guardrails"
            subtitle="PII · evidence · confidence"
            status={pipeline.outputGuardrail.status}
            checks={pipeline.outputGuardrail.decisions}
            time={pipeline.outputGuardrail.time}
          />
          <div className="tier-inline-arrow" />
          <TraceNode
            compact={compact}
            type="result"
            num={8}
            title="Result"
            status={resultStatus}
            subtitle={pipeline.done && !pipeline.done.allowed ? `blocked: ${pipeline.done.stageBlocked}` : undefined}
            time={pipeline.done?.time}
          />
        </div>
      </Tier>
    </div>
  )
}
