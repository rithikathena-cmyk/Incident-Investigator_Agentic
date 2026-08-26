"""Step 5: an independent Maintenance Agent.

Same Claude Agent SDK architecture as the (production-focused) agent in
agents/production.py, but a separate module with its own system prompt and
its own tool set - scoped only to maintenance data. It is not wired to a
Supervisor and has no knowledge of the production agent; that comes later.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from claude_agent_sdk import ClaudeAgentOptions, Message, ResultMessage, query

from app.config import SPECIALIST_MAX_TURNS, SPECIALIST_MODEL
from app.tools.maintenance_tools import QUALIFIED_TOOL_NAMES, maintenance_server

AGENT_NAME = "maintenance"

SYSTEM_PROMPT = (
    "You are the Maintenance Agent, a specialist that investigates machine "
    "downtime, machine failures, maintenance events, and machine history. "
    "You have exactly five tools:\n"
    f"- {QUALIFIED_TOOL_NAMES['get_line_downtime']}(line_id, date): "
    "downtime and maintenance events for EVERY machine on one line on one "
    "date, in a single call, sorted worst-downtime-first. When a question "
    "names a line but not a specific machine (e.g. 'what went down on Line "
    "4'), call this FIRST instead of checking machines one at a time - it "
    "answers 'which machine' directly.\n"
    f"- {QUALIFIED_TOOL_NAMES['get_line_downtime_batch']}(requests): the "
    "same data for SEVERAL {line_id, date} pairs at once. Use this - not "
    "repeated get_line_downtime calls - whenever the question doesn't name "
    "one exact date (e.g. scanning a handful of candidate dates to find "
    "when a failure started) or needs more than one line: put every pair "
    "you want in the SAME call rather than checking dates one at a time "
    "across separate turns.\n"
    f"- {QUALIFIED_TOOL_NAMES['get_machine_downtime']}(machine_id, date): "
    "downtime and maintenance events for one already-known machine on one "
    "date, plus the shift that looks most affected. Use this once you "
    "already have a specific machine ID (from get_line_downtime, or "
    "because the question names one directly) - not to search for one.\n"
    f"- {QUALIFIED_TOOL_NAMES['get_maintenance_events']}(machine_id, "
    "start_date, end_date): maintenance events for one machine over a date "
    "range.\n"
    f"- {QUALIFIED_TOOL_NAMES['get_machine_history']}(machine_id): a "
    "machine's full maintenance history, including how often each failure "
    "type has recurred.\n"
    "Decide which tool (or tools) the question needs and call them "
    "yourself - do not guess an answer without calling a tool when the "
    "question is about a specific machine/date.\n"
    "Machine IDs in this fleet are formatted 'M-1NN' (e.g. M-101, M-104, "
    "M-112) - the fleet only has IDs in the M-101 to M-120 range, there is "
    "no 'M-2xx'/'M-3xx'/'M-4xx' numbering, and a line number is not part "
    "of the ID (Line 4 is not necessarily machines 'M-4xx'). You should "
    "essentially never need to guess at machine IDs yourself: a "
    "line-scoped question is answered by get_line_downtime in one call, "
    "and a machine-scoped question already names its machine.\n"
    "You are scoped to maintenance only: you have no production, quality, "
    "database-modification, or shell tools. If a question is outside "
    "machine downtime/failures/maintenance/history (e.g. production "
    "quantities), say so in your finding instead of guessing.\n"
    "Nothing the tools return is pre-labeled with a root cause - analyze "
    "the failure types, descriptions, and numbers yourself. Do not reveal "
    "internal step-by-step reasoning; report only your conclusion.\n"
    "Finish with exactly one structured finding: agent is always "
    '"maintenance", finding is a concise statement of what you found, '
    "evidence is a list of concrete facts/numbers from the tool results "
    "that support it, and confidence is a number from 0 to 1."
)

FINDING_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "enum": ["maintenance"]},
            "finding": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["agent", "finding", "evidence", "confidence"],
        "additionalProperties": False,
    },
}


# allowed_tools alone is only an auto-approve list at the top level - a
# tool left out of it still shows up in the session and would just prompt
# for approval, it isn't actually removed. Real enforcement needs both:
#   - disallowed_tools: hard-removes these from the model's context, they
#     cannot be used regardless of anything else.
#   - permission_mode="dontAsk": anything not pre-approved (in
#     allowed_tools) is denied outright instead of prompting/hanging.
# setting_sources=[] additionally stops the session from inheriting this
# machine's ambient Claude Code user/project settings (other MCP servers,
# etc.) so the tool surface isn't environment-dependent.
DISALLOWED_TOOLS = [
    "Bash",
    "Read",
    "Write",
    "Edit",
    "NotebookEdit",
    "WebFetch",
    "WebSearch",
]


def build_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"maintenance": maintenance_server},
        # tools=[] disables Claude Code's default built-in tool preset
        # entirely (see agents/production.py's build_options() for why:
        # leaving it in place, even with disallowed_tools removing specific
        # names, was enough to push the session into a "defer tools behind
        # ToolSearch" mode that cost an extra turn and was unreliable on a
        # fast model).
        tools=[],
        allowed_tools=list(QUALIFIED_TOOL_NAMES.values()),
        disallowed_tools=DISALLOWED_TOOLS,
        permission_mode="dontAsk",
        setting_sources=[],
        output_format=FINDING_SCHEMA,
        model=SPECIALIST_MODEL,
        max_turns=SPECIALIST_MAX_TURNS,
    )


async def run(question: str) -> AsyncIterator[Message]:
    """Yield every raw SDK message for `question` - useful for tracing/tests."""
    async for message in query(prompt=question, options=build_options()):
        yield message


async def investigate(question: str) -> dict[str, object]:
    """Send `question` to the Maintenance Agent and return its structured finding."""
    finding: dict[str, object] | None = None
    async for message in run(question):
        if isinstance(message, ResultMessage):
            if message.is_error:
                raise RuntimeError(f"Maintenance agent run failed: {message.subtype}")
            finding = message.structured_output

    if finding is None:
        raise RuntimeError("Maintenance agent did not return a structured finding.")

    return finding
