// Pure reducer turning the backend's WebSocket event stream (see
// app/server.py + app/guarded_investigation.py's on_event hook) into a
// renderable pipeline state. No React here on purpose - easy to unit
// reason about independent of the UI layer.

const AGENT_LABELS = {
  production: 'Production Agent',
  maintenance: 'Maintenance Agent',
  quality: 'Quality Agent',
  knowledge: 'Knowledge Agent',
}

export function initialPipeline() {
  return {
    inputGuardrail: { status: 'pending', decisions: [], step: null, time: null },
    rbac: { status: 'pending', decision: null, domains: [], step: null, time: null },
    supervisor: { status: 'pending', planNotes: [], synthesisNotes: [], step: null, time: null },
    synthesis: { time: null },
    agentOrder: [],
    agents: {},
    outputGuardrail: { status: 'pending', hasWarnings: false, decisions: [], step: null, time: null },
    blockedStage: null,
    done: null,
    log: [],
    stepCounter: 0,
  }
}

function ensureAgent(agents, agentOrder, agent) {
  if (!agents[agent]) {
    agents[agent] = { status: 'active', requestQuestion: null, toolCalls: [], finding: null, step: null, startedAt: new Date(), time: null }
    agentOrder.push(agent)
  }
}

function nextStep(state) {
  return state.stepCounter + 1
}

function logEntry(step, text, level = 'info') {
  return { id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, step, text, level, time: new Date() }
}

// Renders a tool call's input for the Detail Log. Plain scalar args (the
// original single-item tools, e.g. {line_id, date}) just join their values.
// The batch tools added later (get_production_metrics_batch,
// get_quality_metrics_batch, get_line_downtime_batch) take a `requests`
// array of {line_id, date} objects - without this branch, joining an array
// of objects falls back to Array.prototype.toString on each item, which
// prints "[object Object]" instead of the actual values.
function formatToolArgs(input) {
  return Object.values(input || {})
    .map((value) => {
      if (Array.isArray(value)) {
        return value
          .map((item) => (item && typeof item === 'object' ? Object.values(item).join('/') : item))
          .join(', ')
      }
      return value
    })
    .join(', ')
}

export function applyEvent(state, event) {
  switch (event.type) {
    case 'input_guardrail': {
      const allowed = event.decisions.every((d) => d.allowed)
      const step = nextStep(state)
      const failed = event.decisions.filter((d) => !d.allowed).map((d) => d.check)
      return {
        ...state,
        stepCounter: step,
        inputGuardrail: { status: allowed ? 'done' : 'blocked', decisions: event.decisions, step, time: new Date() },
        log: [
          ...state.log,
          logEntry(
            step,
            allowed ? 'Input guardrails passed (prompt injection, scope, PII, harmful intent).' : `Input guardrails DENIED: ${failed.join(', ')}.`,
            allowed ? 'info' : 'error',
          ),
        ],
      }
    }

    case 'rbac': {
      const step = nextStep(state)
      return {
        ...state,
        stepCounter: step,
        rbac: { status: event.decision.allowed ? 'done' : 'blocked', decision: event.decision, domains: event.domains, step, time: new Date() },
        log: [
          ...state.log,
          logEntry(
            step,
            event.decision.allowed
              ? `RBAC passed for domain(s): ${event.domains.join(', ') || 'none required'}.`
              : `RBAC DENIED: ${event.decision.reason}`,
            event.decision.allowed ? 'info' : 'error',
          ),
        ],
      }
    }

    case 'blocked': {
      return state
    }

    case 'supervisor_text': {
      const supervisor = { ...state.supervisor, status: 'active' }
      if (supervisor.step == null) {
        supervisor.step = nextStep(state)
        supervisor.time = new Date()
      }
      if (event.phase === 'plan') {
        supervisor.planNotes = [...supervisor.planNotes, event.text]
      } else {
        supervisor.synthesisNotes = [...supervisor.synthesisNotes, event.text]
      }
      return { ...state, stepCounter: Math.max(state.stepCounter, supervisor.step), supervisor }
    }

    case 'agent_selected': {
      const agents = { ...state.agents }
      const agentOrder = [...state.agentOrder]
      ensureAgent(agents, agentOrder, event.agent)
      const supervisor = { ...state.supervisor, status: 'active' }
      let stepCounter = state.stepCounter
      if (supervisor.step == null) {
        supervisor.step = nextStep(state)
        supervisor.time = new Date()
        stepCounter = supervisor.step
      }
      if (agents[event.agent].step == null) {
        const step = stepCounter + 1
        agents[event.agent] = { ...agents[event.agent], step }
        stepCounter = step
      }
      return {
        ...state,
        stepCounter,
        supervisor,
        agents,
        agentOrder,
        log: [...state.log, logEntry(agents[event.agent].step, `Supervisor delegated to ${AGENT_LABELS[event.agent] || event.agent}.`)],
      }
    }

    case 'agent_request': {
      const agents = { ...state.agents }
      const agentOrder = [...state.agentOrder]
      ensureAgent(agents, agentOrder, event.agent)
      agents[event.agent] = { ...agents[event.agent], requestQuestion: event.question, status: 'active' }
      return { ...state, agents, agentOrder }
    }

    case 'tool_call': {
      const agents = { ...state.agents }
      const agentOrder = [...state.agentOrder]
      ensureAgent(agents, agentOrder, event.agent)
      const agent = { ...agents[event.agent] }
      agent.toolCalls = [...agent.toolCalls, { tool: event.tool, input: event.input, status: 'active', result: null }]
      agent.status = 'active'
      agents[event.agent] = agent
      return {
        ...state,
        agents,
        agentOrder,
        log: [...state.log, logEntry(agent.step, `${AGENT_LABELS[event.agent] || event.agent} called ${event.tool}(${formatToolArgs(event.input)}).`)],
      }
    }

    case 'tool_result': {
      const agents = { ...state.agents }
      const agent = agents[event.agent]
      if (!agent) return state
      const calls = [...agent.toolCalls]
      const reverseIdx = [...calls].reverse().findIndex((c) => c.tool === event.tool && c.status === 'active')
      if (reverseIdx !== -1) {
        const idx = calls.length - 1 - reverseIdx
        calls[idx] = { ...calls[idx], status: event.is_error ? 'error' : 'done', result: event.summary }
      }
      agents[event.agent] = { ...agent, toolCalls: calls }
      return {
        ...state,
        agents,
        log: [
          ...state.log,
          logEntry(
            agent.step,
            event.is_error ? `${event.tool} returned no data: ${event.summary}` : `${event.tool} returned a result.`,
            event.is_error ? 'warn' : 'info',
          ),
        ],
      }
    }

    case 'agent_finding': {
      const agents = { ...state.agents }
      const agent = agents[event.agent]
      if (!agent) return state
      agents[event.agent] = {
        ...agent,
        status: 'done',
        time: new Date(),
        finding: { finding: event.finding, evidence: event.evidence, confidence: event.confidence },
      }
      return {
        ...state,
        agents,
        log: [
          ...state.log,
          logEntry(agent.step, `${AGENT_LABELS[event.agent] || event.agent} finished (${Math.round(event.confidence * 100)}% confidence).`),
        ],
      }
    }

    case 'trace_finalized': {
      return {
        ...state,
        supervisor: { ...state.supervisor, status: 'done' },
        synthesis: { time: new Date() },
      }
    }

    case 'output_guardrail': {
      const hasWarnings = event.decisions.some((d) => d.decision === 'WARN')
      const step = nextStep(state)
      return {
        ...state,
        stepCounter: step,
        outputGuardrail: { status: 'done', hasWarnings, decisions: event.decisions, step, time: new Date() },
        log: [
          ...state.log,
          logEntry(step, hasWarnings ? 'Output guardrails applied redactions/warnings.' : 'Output guardrails passed cleanly.', hasWarnings ? 'warn' : 'info'),
        ],
      }
    }

    case 'done': {
      const step = nextStep(state)
      return {
        ...state,
        stepCounter: step,
        done: {
          allowed: event.allowed,
          stageBlocked: event.stage_blocked,
          report: event.report,
          trace: event.trace,
          step,
          time: new Date(),
        },
        log: [...state.log, logEntry(step, event.allowed ? 'Investigation complete.' : `Investigation blocked at ${event.stage_blocked}.`, event.allowed ? 'success' : 'error')],
      }
    }

    default:
      return state
  }
}
