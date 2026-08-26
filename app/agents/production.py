"""Step 3: the agent gains one custom tool, get_production_metrics."""

from __future__ import annotations

from collections.abc import AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    Message,
    ResultMessage,
    TextBlock,
    query,
)

from app.config import SPECIALIST_MAX_TURNS, SPECIALIST_MODEL
from app.tools.production_tools import (
    QUALIFIED_BATCH_TOOL_NAME,
    QUALIFIED_TOOL_NAME,
    production_server,
)

AGENT_NAME = "Manufacturing Investigator"

SYSTEM_PROMPT = (
    "You are the Manufacturing Investigator, an assistant that helps engineers "
    "investigate manufacturing production incidents. You have two tools:\n"
    f"- {QUALIFIED_TOOL_NAME}(line_id, date): real production data (planned "
    "vs. actual quantity, downtime, shift breakdown) for ONE line and ONE "
    "date.\n"
    f"- {QUALIFIED_BATCH_TOOL_NAME}(requests): the same data for SEVERAL "
    "line/date pairs at once (requests is an array of {line_id, date}). Use "
    "this - not repeated single calls - whenever you want more than one "
    "line/date combination (e.g. comparing the incident date against a few "
    "prior days, or checking more than one line): put every pair you want "
    "in the SAME call rather than calling the single-item tool once per "
    "pair and waiting for each before starting the next.\n"
    "Call one of these only when the question needs actual production "
    "numbers for specific line(s)/date(s). For anything else - general "
    "questions, or questions with no clear line/date - answer directly "
    "without calling either."
)


# allowed_tools alone is only an auto-approve list at the top level - it
# does not remove other tools from the session (discovered while building
# the Step 5 Maintenance Agent's permission boundary). disallowed_tools
# hard-removes them, permission_mode="dontAsk" denies-by-default anything
# else not pre-approved, and setting_sources=[] stops the session from
# inheriting this machine's ambient Claude Code settings.
DISALLOWED_TOOLS = ["Bash", "Read", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch"]

# tools=[] (performance): build_options() also passes tools=[], which
# disables Claude Code's entire default built-in tool preset (Bash, Read,
# Grep, Glob, Task, ... dozens of tools) rather than leaving it in place
# and merely disallowing a few names. That preset being present at all - even
# with every one of its tools individually disallowed - was enough to push
# the CLI into its automatic "defer tools behind a searchable index" mode
# (a ToolSearch tool call before the real one), which cost every specialist
# session a full extra turn, and on a fast model was measured to sometimes
# never resolve at all (repeated ToolSearch calls until max_turns was hit,
# with the real domain tool never invoked). tools=[] plus the one MCP
# server registered in mcp_servers= below is the smallest change that both
# removes that failure mode and shrinks the tool surface further (strictly
# tighter than disallowed_tools alone, not looser).


def build_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"production": production_server},
        tools=[],
        allowed_tools=[QUALIFIED_TOOL_NAME, QUALIFIED_BATCH_TOOL_NAME],
        disallowed_tools=DISALLOWED_TOOLS,
        permission_mode="dontAsk",
        setting_sources=[],
        model=SPECIALIST_MODEL,
        max_turns=SPECIALIST_MAX_TURNS,
    )


async def run(question: str) -> AsyncIterator[Message]:
    """Yield every raw SDK message for `question` (assistant text, tool use,
    tool results, final result) - the trace `investigate()` collapses away.
    """
    async for message in query(prompt=question, options=build_options()):
        yield message


async def investigate(question: str) -> str:
    """Send `question` to the agent and return its final text answer."""
    answer_parts: list[str] = []
    async for message in run(question):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    answer_parts.append(block.text)
        elif isinstance(message, ResultMessage):
            if message.is_error:
                raise RuntimeError(f"Agent run failed: {message.subtype}")

    return "\n".join(answer_parts)
