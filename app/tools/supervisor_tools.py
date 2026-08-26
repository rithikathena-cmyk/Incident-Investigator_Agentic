"""Supervisor delegation tool (Step 8 + Step 9 + Phase 11 tracing + Phase 15
concurrent delegation).

The Supervisor has exactly one way to reach a specialist:
delegate_to_specialists, which takes an ARRAY of {agent,
investigation_question, context} and runs every item concurrently via
asyncio.gather(). This replaces the original four separate
delegate_to_X_agent tools (Steps 8-14): with four separate tools, the
model reliably called them one at a time across separate turns even when
explicitly told it could call several together in one response - verified
empirically (see README) - so the whole investigation ran as one long
sequential chain (Production, *then* Maintenance, *then* Quality, *then*
Knowledge) even for a question where none of them depend on each other's
findings. Collapsing delegation into a single tool with an array input
removes the model's opportunity to fall back into that one-at-a-time habit:
there is no other way to delegate, and asyncio.gather() makes concurrency a
guarantee of this handler's own code rather than something hoped for from
the model's tool-call batching behavior.

For each item in the array, in this exact order:

1. The delegation gate: capabilities.authorize_delegation("supervisor", X)
   - a deterministic Python check, not an LLM judgment call. If denied,
     that specialist is never invoked (the other items in the same call
     are unaffected).
2. The request is recorded into the current InvestigationTrace, if any
   (Phase 11) - what was asked, of which agent.
3. The specialist's OWN, already-independently-built-and-hardened run()
   coroutine runs as a completely separate Claude Agent SDK session - its
   own system prompt, its own tools/mcp_servers, its own
   disallowed_tools/permission_mode/setting_sources (agents/production.py,
   agents/maintenance.py, agents/quality.py, agents/knowledge.py are not
   modified). There is no shared session, so there is no shared tool list
   for one specialist's tools to leak into another's. The raw message
   stream is wrapped by tracing.trace_specialist_run() so its own tool
   calls/results get recorded into the trace too, then the final
   structured finding is extracted and also recorded.

Why not the SDK's native subagent/AgentDefinition mechanism? It was tried
first (see README) - a subagent's own `tools` allowlist turned out not to
be a true, exclusive allowlist in practice (it still attempted Bash
unprompted in testing), and giving it working access to its own tool
required also adding that tool to the *top-level* Supervisor's
allowed_tools - which risks the Supervisor calling PostgreSQL/Qdrant tools
directly, the opposite of what's required here. Delegating via a plain
custom tool that calls each specialist's own isolated session sidesteps
that ambiguity entirely and is what actually enforces "the Supervisor must
not receive direct database access": the Supervisor's own session never
has a production/maintenance/quality/knowledge MCP server registered on it
at all.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, Message, ResultMessage, create_sdk_mcp_server, query, tool

from app import tracing
from app.agents.production import DISALLOWED_TOOLS as PRODUCTION_DISALLOWED_TOOLS
from app.agents.production import SYSTEM_PROMPT as PRODUCTION_SYSTEM_PROMPT
from app.agents.knowledge import run as run_knowledge
from app.agents.maintenance import run as run_maintenance
from app.agents.quality import run as run_quality
from app.config import SPECIALIST_MAX_TURNS, SPECIALIST_MODEL
from app.guardrails import capabilities
from app.tools.production_tools import QUALIFIED_BATCH_TOOL_NAME as PRODUCTION_BATCH_TOOL_NAME
from app.tools.production_tools import QUALIFIED_TOOL_NAME as PRODUCTION_TOOL_NAME
from app.tools.production_tools import production_server

SUPERVISOR_SERVER_NAME = "supervisor_delegation"

DELEGATE_TO_SPECIALISTS = "delegate_to_specialists"
QUALIFIED_DELEGATE_TOOL_NAME = f"mcp__{SUPERVISOR_SERVER_NAME}__{DELEGATE_TO_SPECIALISTS}"

AGENT_NAMES = ("production", "maintenance", "quality", "knowledge")


def _combine(investigation_question: str, context: object) -> str:
    if context and str(context).strip():
        return f"{investigation_question}\n\nAdditional context: {context}"
    return investigation_question


async def _collect_finding(agent: str, message_stream: AsyncIterator[Message]) -> dict[str, Any]:
    """Consume a specialist's traced message stream and return its final
    structured finding. Tool calls/results pass through
    tracing.trace_specialist_run() and are recorded as they go.
    """
    finding: dict[str, Any] | None = None
    async for message in tracing.trace_specialist_run(agent, message_stream):
        if isinstance(message, ResultMessage):
            if message.is_error:
                raise RuntimeError(f"{agent} agent run failed: {message.subtype}")
            finding = message.structured_output

    if finding is None:
        raise RuntimeError(f"{agent} agent did not return a structured finding.")
    return finding


def _record_delegation(agent: str, investigation_question: str, context: object) -> None:
    trace = tracing.current_trace.get()
    if trace is not None:
        trace.record_agent_selected(agent)
        trace.record_request(agent, investigation_question, context)


def _record_finding(agent: str, finding: dict[str, Any]) -> None:
    trace = tracing.current_trace.get()
    if trace is not None:
        trace.record_finding(
            agent,
            str(finding.get("finding", "")),
            list(finding.get("evidence", [])),
            float(finding.get("confidence", 0.0)),
        )


# --- Production: agents/production.py has no structured-finding
# output_format of its own (it predates that convention and is kept
# unmodified for Steps 2-4's CLI contract), so the Supervisor's own
# delegation path adds one here, reusing its exact prompt/tool/server/
# hardening unchanged.

_PRODUCTION_DELEGATION_SUFFIX = (
    "\n\nWhen you are invoked as a delegated subagent by the Supervisor, "
    "end your response with exactly one structured finding: agent is "
    'always "production", finding is a concise statement of what you '
    "found, evidence is a list of concrete facts/numbers that support it, "
    "and confidence is a number from 0 to 1."
)

_PRODUCTION_FINDING_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "enum": ["production"]},
            "finding": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["agent", "finding", "evidence", "confidence"],
        "additionalProperties": False,
    },
}


async def _run_production(question: str) -> AsyncIterator[Message]:
    options = ClaudeAgentOptions(
        system_prompt=PRODUCTION_SYSTEM_PROMPT + _PRODUCTION_DELEGATION_SUFFIX,
        mcp_servers={"production": production_server},
        # tools=[] disables Claude Code's default built-in tool preset
        # entirely (see agents/production.py's build_options() for why).
        tools=[],
        allowed_tools=[PRODUCTION_TOOL_NAME, PRODUCTION_BATCH_TOOL_NAME],
        disallowed_tools=PRODUCTION_DISALLOWED_TOOLS,
        permission_mode="dontAsk",
        setting_sources=[],
        output_format=_PRODUCTION_FINDING_SCHEMA,
        model=SPECIALIST_MODEL,
        max_turns=SPECIALIST_MAX_TURNS,
    )
    async for message in query(prompt=question, options=options):
        yield message


_AGENT_RUNNERS = {
    "production": _run_production,
    "maintenance": run_maintenance,
    "quality": run_quality,
    "knowledge": run_knowledge,
}


async def _delegate_one(agent: str, investigation_question: str, context: object) -> dict[str, Any]:
    """Run the full delegation flow for one specialist: capability gate,
    trace the request, run its isolated session, trace the finding. Never
    raises - failures (denial or a runtime error from the specialist run)
    come back as a dict the caller can distinguish via "error", so one
    failing delegation inside an asyncio.gather() batch doesn't cancel the
    others.
    """
    if agent not in AGENT_NAMES:
        return {"agent": agent, "error": True, "detail": f"Unknown agent '{agent}'."}

    decision = capabilities.authorize_delegation("supervisor", agent)
    if not decision.allowed:
        return {"agent": agent, "error": True, "detail": decision.to_dict()}

    _record_delegation(agent, investigation_question, context)
    question = _combine(investigation_question, context)
    try:
        finding = await _collect_finding(agent, _AGENT_RUNNERS[agent](question))
    except Exception as exc:  # noqa: BLE001 - reported to the model, not raised
        return {"agent": agent, "error": True, "detail": str(exc)}

    _record_finding(agent, finding)
    return {"agent": agent, "error": False, "finding": finding}


_DELEGATIONS_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "delegations": {
            "type": "array",
            "description": (
                "One or more specialists to investigate. Every item runs "
                "concurrently in this single call - always include every "
                "specialist you can already ask a complete, self-contained "
                "question for right now, rather than calling this tool "
                "again for each one in turn."
            ),
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "agent": {
                        "type": "string",
                        "enum": list(AGENT_NAMES),
                        "description": (
                            "production: production quantity, production loss, production "
                            "trends, shift performance. maintenance: machine downtime, "
                            "maintenance events, machine failures, machine history. quality: "
                            "rejection rate, defects, quality trends, quality anomalies. "
                            "knowledge: maintenance documentation, SOPs, operating "
                            "procedures, technical knowledge."
                        ),
                    },
                    "investigation_question": {
                        "type": "string",
                        "description": "The specific question for this specialist to investigate. It cannot see this conversation - make it self-contained.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional relevant context: dates, line/machine IDs, prior findings.",
                    },
                },
                "required": ["agent", "investigation_question"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["delegations"],
    "additionalProperties": False,
}


@tool(
    DELEGATE_TO_SPECIALISTS,
    "Delegate to one or more specialist agents at once - production, "
    "maintenance, quality, knowledge (see each agent's description in the "
    "delegations.agent field). Pass every specialist you can already ask a "
    "complete, self-contained question for in ONE call to this tool: they "
    "run concurrently, which is far faster than calling this tool once per "
    "specialist and waiting for each before starting the next. Only call "
    "it again later for a specialist whose question genuinely depends on "
    "a finding you don't have yet.",
    _DELEGATIONS_INPUT_SCHEMA,
)
async def delegate_to_specialists(args: dict[str, Any]) -> dict[str, Any]:
    delegations = args.get("delegations") or []
    if not delegations:
        return {"content": [{"type": "text", "text": "delegations must contain at least one item."}], "is_error": True}

    results = await asyncio.gather(
        *(
            _delegate_one(str(d["agent"]), str(d["investigation_question"]), d.get("context"))
            for d in delegations
        )
    )

    payload = {r["agent"]: (r["detail"] if r["error"] else r["finding"]) for r in results}
    all_failed = all(r["error"] for r in results)
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
        "is_error": all_failed,
    }


supervisor_server = create_sdk_mcp_server(
    name=SUPERVISOR_SERVER_NAME,
    version="2.0.0",
    tools=[delegate_to_specialists],
)
