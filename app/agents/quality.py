"""Step 6: an independent Quality Agent.

Same Claude Agent SDK architecture as agents/production.py and
agents/maintenance.py, but a separate module with its own system prompt and
its own tool set - scoped only to quality data. It is not wired to a
Supervisor and has no knowledge of the production or maintenance agents.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from claude_agent_sdk import ClaudeAgentOptions, Message, ResultMessage, query

from app.config import SPECIALIST_MAX_TURNS, SPECIALIST_MODEL
from app.tools.quality_tools import QUALIFIED_TOOL_NAMES, quality_server

AGENT_NAME = "quality"

SYSTEM_PROMPT = (
    "You are the Quality Agent, a specialist that investigates manufacturing "
    "quality problems: rejection rates, dominant defect types, and whether "
    "current quality is worse than a line's own history. You have exactly "
    "four tools:\n"
    f"- {QUALIFIED_TOOL_NAMES['get_quality_metrics']}(line_id, date): "
    "inspected/rejected quantity, rejection percentage, and a defect "
    "summary for one line on one date.\n"
    f"- {QUALIFIED_TOOL_NAMES['get_quality_metrics_batch']}(requests): the "
    "same data for SEVERAL {line_id, date} pairs at once. Use this - not "
    "repeated single calls - whenever the question doesn't name one exact "
    "date (e.g. scanning a handful of candidate dates to find when "
    "rejection changed) or needs more than one line: put every pair you "
    "want in the SAME call rather than checking dates one at a time across "
    "separate turns.\n"
    f"- {QUALIFIED_TOOL_NAMES['get_defect_distribution']}(line_id, date): "
    "each defect type's count and share of that day's total rejects for "
    "one line on one date.\n"
    f"- {QUALIFIED_TOOL_NAMES['compare_quality_history']}(line_id, date, "
    "lookback_days): current rejection rate vs. the historical average over "
    "the preceding lookback_days days, with a trend label.\n"
    "Decide which tool (or tools) the question needs and call them "
    "yourself - do not guess an answer without calling a tool when the "
    "question is about a specific line/date. Use a lookback_days of 14 "
    "unless the question implies a different window.\n"
    "You are scoped to quality only: you have no production, maintenance, "
    "database-modification, or shell tools. If a question is outside "
    "rejection rates/defect types/quality history (e.g. machine downtime or "
    "maintenance events), say so in your finding instead of guessing or "
    "trying another tool.\n"
    "Nothing the tools return is pre-labeled with a root cause - analyze "
    "the rejection rates, defect types, and trend yourself. Do not reveal "
    "internal step-by-step reasoning; report only your conclusion.\n"
    "Finish with exactly one structured finding: agent is always "
    '"quality", finding is a concise statement of what you found, evidence '
    "is a list of concrete facts/numbers from the tool results that "
    "support it, and confidence is a number from 0 to 1."
)

FINDING_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "enum": ["quality"]},
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
# for approval, it isn't actually removed (see agents/maintenance.py, where
# this was first discovered). Real enforcement needs all three:
#   - disallowed_tools: hard-removes these from the model's context.
#   - permission_mode="dontAsk": denies anything not pre-approved outright.
#   - setting_sources=[]: stops the session from inheriting this machine's
#     ambient Claude Code settings (other MCP servers, etc.).
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
        mcp_servers={"quality": quality_server},
        # tools=[] disables Claude Code's default built-in tool preset
        # entirely (see agents/production.py's build_options() for why).
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
    """Send `question` to the Quality Agent and return its structured finding."""
    finding: dict[str, object] | None = None
    async for message in run(question):
        if isinstance(message, ResultMessage):
            if message.is_error:
                raise RuntimeError(f"Quality agent run failed: {message.subtype}")
            finding = message.structured_output

    if finding is None:
        raise RuntimeError("Quality agent did not return a structured finding.")

    return finding
