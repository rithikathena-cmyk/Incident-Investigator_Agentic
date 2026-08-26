"""Step 8 + Step 9: the Supervisor Agent.

Real multi-agent orchestration: the Supervisor's own Claude session has
four delegation tools (one per specialist) and decides for itself, via the
Agent SDK's normal tool-selection reasoning, which ones a given question
actually needs - there is no Python if/else routing a question to a fixed
set of agents. See tools/supervisor_tools.py for how delegation works and
why it's built on custom tools rather than the SDK's native subagent
mechanism.

The Supervisor never has a production/maintenance/quality/knowledge MCP
server registered on its own session - it structurally cannot query
PostgreSQL or Qdrant directly, only delegate.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any, Callable

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, Message, ResultMessage, TextBlock, query

from app import tracing
from app.config import SUPERVISOR_MAX_TURNS, SUPERVISOR_MODEL
from app.guardrails import capabilities
from app.tools.supervisor_tools import (
    QUALIFIED_DELEGATE_TOOL_NAME,
    SUPERVISOR_SERVER_NAME,
    supervisor_server,
)

AGENT_NAME = "supervisor"

SYSTEM_PROMPT = (
    "You are the Supervisor Agent for a manufacturing incident "
    "investigation system. Given an investigation question, you must:\n"
    "1. Understand what's being asked.\n"
    "2. Decide which specialist agents are relevant, and delegate to all "
    f"of them in ONE call to your only tool, {QUALIFIED_DELEGATE_TOOL_NAME} "
    "- it takes an array, and every item you include runs concurrently, "
    "not one at a time. The tool's own schema describes what each of the "
    "four specialists (production, maintenance, quality, knowledge) "
    "covers. Call ONLY the specialists genuinely relevant to this "
    "question - do not invoke all four out of habit, and do not skip one "
    "that matters. A narrow question (e.g. only about rejection rate) may "
    "need just one specialist; a broad 'why did X drop' question usually "
    "needs several - include all of those several in the SAME call, as "
    "multiple items in the delegations array, rather than calling the "
    "tool again for each one in turn. For example, a 'why did Line 4 drop "
    "on <date>' question can send production, maintenance, and quality "
    "all as three items in your very first call - none of them needs "
    "another's answer to investigate that same line/date within its own "
    "domain. Only call the tool again afterward, for one more specialist, "
    "when that specialist's question genuinely cannot be phrased yet - "
    "for example, send knowledge a specific failure-mode query (e.g. "
    "'drive motor winding failure') only after maintenance has told you "
    "what actually failed, rather than guessing.\n"
    "3. Give each specialist a specific, self-contained "
    "investigation_question (it cannot see this conversation) plus any "
    "relevant context - dates, line/machine IDs, other findings so far - "
    "in its context field.\n"
    "4. Collect each specialist's finding (agent, finding, evidence, "
    "confidence).\n"
    "5. Compare the evidence across specialists - look for whether they "
    "corroborate or contradict a single underlying cause, rather than "
    "just concatenating them.\n"
    "6. Identify the most likely root cause, and separately list "
    "contributing factors that are related but secondary.\n"
    "7. Produce a final investigation report.\n\n"
    "You have no direct database or knowledge-base access yourself - you "
    "can only get real data by delegating, and every delegation passes "
    "through a deterministic capability check you cannot override or "
    "reason your way around; an unauthorized delegation is refused before "
    "any specialist runs.\n\n"
    "Do not claim more certainty than the evidence supports: if only one "
    "specialist's evidence points a certain way, or the evidence is thin "
    "or mixed, say so and lower your confidence rather than asserting a "
    "definite cause. When evidence is genuinely insufficient, say so "
    "explicitly rather than guessing.\n\n"
    "Do not reveal your internal step-by-step reasoning - report only "
    "which specialists you used, a synthesis of what they found, and your "
    "conclusion."
)

FINAL_REPORT_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "agents_used": {"type": "array", "items": {"type": "string"}},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "agent": {"type": "string"},
                        "finding": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["agent", "finding", "evidence", "confidence"],
                    "additionalProperties": False,
                },
            },
            "root_cause": {"type": "string"},
            "contributing_factors": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "question",
            "agents_used",
            "findings",
            "root_cause",
            "contributing_factors",
            "evidence",
            "confidence",
        ],
        "additionalProperties": False,
    },
}

# Same hardening as every other agent in this project (see
# agents/maintenance.py for where this was first discovered). The
# Supervisor's own MCP server only contains the one delegate tool - there
# is nothing production/maintenance/quality/knowledge-shaped for it to
# call directly even in principle.
DISALLOWED_TOOLS = ["Bash", "Read", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch"]


def build_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={SUPERVISOR_SERVER_NAME: supervisor_server},
        # tools=[] disables Claude Code's default built-in tool preset
        # entirely (see agents/production.py's build_options() for why).
        tools=[],
        allowed_tools=[QUALIFIED_DELEGATE_TOOL_NAME],
        disallowed_tools=DISALLOWED_TOOLS,
        permission_mode="dontAsk",
        setting_sources=[],
        output_format=FINAL_REPORT_SCHEMA,
        model=SUPERVISOR_MODEL,
        max_turns=SUPERVISOR_MAX_TURNS,
    )


async def run(question: str, *, investigation_id: str | None = None) -> AsyncIterator[Message]:
    """Yield every raw SDK message for `question` - useful for tracing/tests.

    Sets an investigation_id for the duration of the run so every
    capability decision made while handling this question (including deep
    inside a delegated specialist's own tool calls) is correlated in the
    audit log. Generates a fresh one unless the caller supplies one (used
    by investigate_with_trace() to share an id with its InvestigationTrace).
    """
    investigation_id = investigation_id or capabilities.new_investigation_id()
    token = capabilities.current_investigation_id.set(investigation_id)
    try:
        async for message in query(prompt=question, options=build_options()):
            yield message
    finally:
        capabilities.current_investigation_id.reset(token)


async def investigate(question: str) -> dict[str, object]:
    """Send `question` to the Supervisor and return its final structured
    investigation report.
    """
    report, _trace = await investigate_with_trace(question)
    return report


async def investigate_with_trace(
    question: str,
    *,
    investigation_id: str | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, object], tracing.InvestigationTrace]:
    """Like investigate(), but also builds a Phase 11 InvestigationTrace:
    the Supervisor's own narration (plan/synthesis) plus everything
    tracing.trace_specialist_run() records from inside each delegated
    specialist's own session (their tool calls, results, findings).

    investigate()/run() are unaffected by this - tracing.current_trace is
    None unless this function is used, and every trace-recording call
    checks for that, so plain run()/investigate() callers (existing tests)
    see no behavior change.

    Pass investigation_id to share one id across a guardrail layer's own
    decisions (Phase 12) and this investigation's capability/trace records -
    otherwise a fresh one is generated. Pass on_event (Phase 14's live demo
    UI) to receive every trace event as it's recorded, not just the final
    result - see tracing.InvestigationTrace.on_event.
    """
    investigation_id = investigation_id or capabilities.new_investigation_id()
    trace = tracing.InvestigationTrace(investigation_id=investigation_id, user_question=question, on_event=on_event)
    trace_token = tracing.current_trace.set(trace)
    started = time.monotonic()

    try:
        report: dict[str, object] | None = None
        async for message in run(question, investigation_id=investigation_id):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        trace.record_supervisor_text(block.text)
            elif isinstance(message, ResultMessage):
                if message.is_error:
                    raise RuntimeError(f"Supervisor run failed: {message.subtype}")
                report = message.structured_output
    finally:
        tracing.current_trace.reset(trace_token)

    if report is None:
        raise RuntimeError("Supervisor did not return a structured investigation report.")

    trace.finalize(report, time.monotonic() - started)
    return report, trace
