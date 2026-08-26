"""Structured, human-readable tracing for multi-agent investigations (Phase 11).

Records only observable events - tool calls, tool results, agent findings,
Supervisor narration - never the model's internal reasoning/thinking (which
this project never requests displayed anyway; see every agent's system
prompt: "do not reveal internal step-by-step reasoning").

Uses the same contextvars pattern already verified in capabilities.py: a
single InvestigationTrace, set once at the top of supervisor.run(), is
visible to code running inside nested delegated specialist sessions
because everything executes in the same asyncio process (in-process MCP
tool execution) - confirmed empirically in Step 8/9 testing (33 nested
capability decisions all correctly correlated by investigation_id).
"""

from __future__ import annotations

import contextvars
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable

from claude_agent_sdk import AssistantMessage, Message, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage

_RESULT_SUMMARY_MAX_CHARS = 220
_HARNESS_TOOL_PREFIXES = ("mcp__supervisor_delegation__", "mcp__production__", "mcp__maintenance__", "mcp__quality__", "mcp__knowledge__")


def _short_tool_name(qualified_name: str) -> str:
    """'mcp__maintenance__get_machine_downtime' -> 'get_machine_downtime'."""
    parts = qualified_name.split("__")
    return parts[-1] if len(parts) >= 3 else qualified_name


def _is_domain_tool(qualified_name: str) -> bool:
    """Only our own MCP tools count as observable actions - excludes
    framework/harness tools (ToolSearch, StructuredOutput, etc.).
    """
    return qualified_name.startswith(_HARNESS_TOOL_PREFIXES)


def _summarize_result(block: ToolResultBlock) -> str:
    parts = block.content if isinstance(block.content, list) else [block.content]
    texts = [p.get("text") if isinstance(p, dict) else str(p) for p in parts]
    joined = " ".join(t for t in texts if t).strip()
    if len(joined) > _RESULT_SUMMARY_MAX_CHARS:
        return joined[:_RESULT_SUMMARY_MAX_CHARS] + "..."
    return joined


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ToolCallRecord:
    agent: str
    tool: str
    input: dict[str, Any]
    timestamp: str


@dataclass
class ToolResultRecord:
    agent: str
    tool: str
    is_error: bool
    summary: str
    timestamp: str


@dataclass
class AgentRequestRecord:
    agent: str
    investigation_question: str
    context: str | None
    timestamp: str


@dataclass
class AgentFindingRecord:
    agent: str
    finding: str
    evidence: list[Any]
    confidence: float
    timestamp: str


@dataclass
class InvestigationTrace:
    investigation_id: str
    user_question: str

    supervisor_plan_notes: list[str] = field(default_factory=list)
    supervisor_synthesis_notes: list[str] = field(default_factory=list)
    selected_agents: list[str] = field(default_factory=list)

    agent_requests: list[AgentRequestRecord] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    tool_results: list[ToolResultRecord] = field(default_factory=list)
    agent_findings: list[AgentFindingRecord] = field(default_factory=list)

    final_report: dict[str, Any] | None = None
    execution_time_seconds: float | None = None

    # Optional live-event sink (the demo UI's WebSocket layer, app/server.py)
    # - fired synchronously from every record_*/finalize call below, right
    # after the corresponding field is updated, so a listener sees exactly
    # the same events render()/to_dict() would reflect. None (the default)
    # means "no listener" - every _emit() call becomes a no-op, so this is
    # purely additive and every pre-existing caller/test is unaffected.
    on_event: Callable[[dict[str, Any]], None] | None = field(default=None, repr=False, compare=False)

    def _emit(self, event_type: str, **payload: Any) -> None:
        if self.on_event is not None:
            self.on_event({"type": event_type, "investigation_id": self.investigation_id, **payload})

    # --- recording (called from supervisor.py / supervisor_tools.py) -----

    def record_supervisor_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self.agent_findings:
            self.supervisor_synthesis_notes.append(text)
            phase = "synthesis"
        else:
            self.supervisor_plan_notes.append(text)
            phase = "plan"
        self._emit("supervisor_text", text=text, phase=phase)

    def record_agent_selected(self, agent: str) -> None:
        if agent not in self.selected_agents:
            self.selected_agents.append(agent)
            self._emit("agent_selected", agent=agent)

    def record_request(self, agent: str, investigation_question: str, context: str | None) -> None:
        self.agent_requests.append(
            AgentRequestRecord(agent=agent, investigation_question=investigation_question, context=context, timestamp=_now())
        )
        self._emit("agent_request", agent=agent, question=investigation_question, context=context)

    def record_tool_call(self, agent: str, tool: str, tool_input: dict[str, Any]) -> None:
        self.tool_calls.append(ToolCallRecord(agent=agent, tool=tool, input=tool_input, timestamp=_now()))
        self._emit("tool_call", agent=agent, tool=tool, input=tool_input)

    def record_tool_result(self, agent: str, tool: str, is_error: bool, summary: str) -> None:
        self.tool_results.append(ToolResultRecord(agent=agent, tool=tool, is_error=is_error, summary=summary, timestamp=_now()))
        self._emit("tool_result", agent=agent, tool=tool, is_error=is_error, summary=summary)

    def record_finding(self, agent: str, finding: str, evidence: list[Any], confidence: float) -> None:
        self.agent_findings.append(
            AgentFindingRecord(agent=agent, finding=finding, evidence=evidence, confidence=confidence, timestamp=_now())
        )
        self._emit("agent_finding", agent=agent, finding=finding, evidence=evidence, confidence=confidence)

    def finalize(self, final_report: dict[str, Any], execution_time_seconds: float) -> None:
        self.final_report = final_report
        self.execution_time_seconds = execution_time_seconds
        self._emit("trace_finalized", report=final_report, execution_time_seconds=execution_time_seconds)

    # --- rendering ---------------------------------------------------------

    def render(self) -> str:
        """A human-readable trace in the PHASE-11 example's shape."""
        lines: list[str] = []
        w = lines.append

        w("=" * 78)
        w("INVESTIGATION TRACE")
        w(f"Question: {self.user_question}")
        w(f"Investigation ID: {self.investigation_id}")
        w("=" * 78)
        w("")
        w("SUPERVISOR")
        w("→ PLAN")
        for note in self.supervisor_plan_notes:
            for line in note.splitlines():
                w(f"  {line}")
        w(f"  → selected agents: {', '.join(self.selected_agents) or '(none)'}")
        w("")

        for finding in self.agent_findings:
            agent = finding.agent
            w(f"{agent.upper()} AGENT")
            request = next((r for r in self.agent_requests if r.agent == agent), None)
            if request is not None:
                w(f"→ request: {request.investigation_question}")

            # Calls and results are recorded in matching chronological order
            # per agent (each tool call's result is recorded right after
            # it), so pairing by position within this agent's own calls/
            # results is correct and needs no cross-referencing by tool_use_id.
            agent_calls = [c for c in self.tool_calls if c.agent == agent]
            agent_results = [r for r in self.tool_results if r.agent == agent]
            for i, call in enumerate(agent_calls):
                w(f"→ {call.tool}({json.dumps(call.input)})")
                if i < len(agent_results):
                    result = agent_results[i]
                    status = "ERROR" if result.is_error else "result"
                    w(f"  → {status}: {result.summary}")
            w(f"→ finding: {finding.finding}")
            if finding.evidence:
                w("  evidence:")
                for item in finding.evidence:
                    w(f"    - {item}")
            w(f"  confidence: {finding.confidence}")
            w("")

        w("SUPERVISOR")
        w("→ SYNTHESIS")
        for note in self.supervisor_synthesis_notes:
            for line in note.splitlines():
                w(f"  {line}")
        if self.final_report:
            w(f"→ ROOT CAUSE: {self.final_report.get('root_cause', '')}")
            factors = self.final_report.get("contributing_factors") or []
            if factors:
                w("→ CONTRIBUTING FACTORS:")
                for factor in factors:
                    w(f"  - {factor}")
            w(f"→ CONFIDENCE: {self.final_report.get('confidence')}")
        if self.execution_time_seconds is not None:
            w(f"→ EXECUTION TIME: {self.execution_time_seconds:.1f}s")
        w("=" * 78)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """The same record, as plain data - for storage/inspection/tests."""
        return {
            "investigation_id": self.investigation_id,
            "user_question": self.user_question,
            "supervisor_plan": self.supervisor_plan_notes,
            "selected_agents": self.selected_agents,
            "agent_requests": [r.__dict__ for r in self.agent_requests],
            "tool_calls": [c.__dict__ for c in self.tool_calls],
            "tool_results": [r.__dict__ for r in self.tool_results],
            "agent_findings": [f.__dict__ for f in self.agent_findings],
            "supervisor_synthesis": self.supervisor_synthesis_notes,
            "final_report": self.final_report,
            "execution_time_seconds": self.execution_time_seconds,
        }


current_trace: contextvars.ContextVar["InvestigationTrace | None"] = contextvars.ContextVar(
    "current_trace", default=None
)


async def trace_specialist_run(agent: str, message_stream: AsyncIterator[Message]) -> AsyncIterator[Message]:
    """Wrap a specialist's raw message stream: record its own tool calls and
    results into the current investigation trace (if any) as they pass
    through, forwarding every message unchanged.
    """
    trace = current_trace.get()
    pending_tool_names: dict[str, str] = {}

    async for message in message_stream:
        if trace is not None:
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock) and _is_domain_tool(block.name):
                        pending_tool_names[block.id] = block.name
                        trace.record_tool_call(agent, _short_tool_name(block.name), block.input)
            elif isinstance(message, UserMessage):
                content = message.content if isinstance(message.content, list) else []
                for block in content:
                    if isinstance(block, ToolResultBlock) and block.tool_use_id in pending_tool_names:
                        tool_name = pending_tool_names.pop(block.tool_use_id)
                        trace.record_tool_result(
                            agent, _short_tool_name(tool_name), bool(block.is_error), _summarize_result(block)
                        )
        yield message
