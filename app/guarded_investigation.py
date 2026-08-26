"""Phase 12: the guardrail + RBAC layer wrapped around the Supervisor.

    USER
     -> INPUT GUARDRAILS   (guardrails.check_input - prompt injection, scope, PII, harmful intent)
     -> RBAC                (guardrails.authorize_domains)
     -> SUPERVISOR AGENT
     -> SPECIALIZED AGENTS
     -> CAPABILITY CHECK    (capabilities.authorize - already enforced inside every tool, Step 9)
     -> TOOLS
     -> AGENT FINDINGS
     -> SUPERVISOR
     -> OUTPUT GUARDRAILS  (guardrails.check_output - PII, evidence requirement, confidence calibration)
     -> USER

Every security decision is deterministic Python (guardrails.py /
capabilities.py) - Claude is never asked whether a request is safe,
injected, in-scope, authorized, or well-evidenced. If input guardrails or
RBAC deny a request, the Supervisor is never invoked at all: no LLM call
happens, no delegation is possible, no tool can execute. This module itself
has no Claude Agent SDK import for the same reason capabilities.py and
guardrails.py don't - it only orchestrates calls into agents/supervisor.py,
which is where the one LLM-calling boundary in this whole layer lives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app import tracing
from app.agents.supervisor import investigate_with_trace
from app.guardrails import capabilities, guardrails


@dataclass
class GuardedInvestigationResult:
    allowed: bool
    stage_blocked: str | None  # "input" | "rbac" | None (never blocked)
    investigation_id: str
    guardrail_decisions: list[guardrails.GuardrailDecision] = field(default_factory=list)
    report: dict[str, Any] | None = None
    trace: tracing.InvestigationTrace | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "stage_blocked": self.stage_blocked,
            "investigation_id": self.investigation_id,
            "guardrail_decisions": [d.to_dict() for d in self.guardrail_decisions],
            "report": self.report,
        }


async def investigate_guarded(
    question: str,
    *,
    user_role: str = guardrails.DEFAULT_ROLE,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> GuardedInvestigationResult:
    """The full guarded pipeline. Returns a result that is either blocked
    (allowed=False, stage_blocked set, report=None - the Supervisor never
    ran) or a completed, guardrail-checked investigation.

    Pass on_event (Phase 14's live demo UI) to receive every stage of the
    pipeline - input guardrails, RBAC, then every agent/tool event forwarded
    from investigate_with_trace(), then output guardrails, then a final
    "done" event - as it happens, not just the returned result.
    """

    def emit(event_type: str, **payload: Any) -> None:
        if on_event is not None:
            on_event({"type": event_type, **payload})

    investigation_id = capabilities.new_investigation_id()

    input_decisions = guardrails.check_input(question, investigation_id=investigation_id)
    emit("input_guardrail", investigation_id=investigation_id, decisions=[d.to_dict() for d in input_decisions])
    if any(not d.allowed for d in input_decisions):
        emit("blocked", investigation_id=investigation_id, stage="input", decisions=[d.to_dict() for d in input_decisions])
        emit("done", investigation_id=investigation_id, allowed=False, stage_blocked="input", report=None, trace=None)
        return GuardedInvestigationResult(
            allowed=False,
            stage_blocked="input",
            investigation_id=investigation_id,
            guardrail_decisions=input_decisions,
        )

    domains = guardrails.classify_domains(question)
    rbac_decision = guardrails.authorize_domains(user_role, domains, investigation_id=investigation_id)
    emit("rbac", investigation_id=investigation_id, decision=rbac_decision.to_dict(), domains=sorted(domains))
    if not rbac_decision.allowed:
        emit("blocked", investigation_id=investigation_id, stage="rbac", decisions=[rbac_decision.to_dict()])
        emit("done", investigation_id=investigation_id, allowed=False, stage_blocked="rbac", report=None, trace=None)
        return GuardedInvestigationResult(
            allowed=False,
            stage_blocked="rbac",
            investigation_id=investigation_id,
            guardrail_decisions=[*input_decisions, rbac_decision],
        )

    report, trace = await investigate_with_trace(question, investigation_id=investigation_id, on_event=on_event)

    output_decisions, redacted_report = guardrails.check_output(report, investigation_id=investigation_id)
    emit("output_guardrail", investigation_id=investigation_id, decisions=[d.to_dict() for d in output_decisions])
    emit(
        "done",
        investigation_id=investigation_id,
        allowed=True,
        stage_blocked=None,
        report=redacted_report,
        trace=trace.to_dict(),
    )

    return GuardedInvestigationResult(
        allowed=True,
        stage_blocked=None,
        investigation_id=investigation_id,
        guardrail_decisions=[*input_decisions, rbac_decision, *output_decisions],
        report=redacted_report,
        trace=trace,
    )
