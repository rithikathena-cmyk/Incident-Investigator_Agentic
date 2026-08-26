"""Step 7: an independent Knowledge/RAG Agent.

Same Claude Agent SDK architecture as agents/production.py,
agents/maintenance.py, and agents/quality.py, but a separate module scoped
only to the manufacturing knowledge base. It is not wired to a Supervisor
and has no knowledge of the other three agents.

The RAG pipeline itself (app.rag.*) has zero Claude Agent SDK dependency -
this agent reaches it only through the search_manufacturing_knowledge tool,
exactly as required.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from claude_agent_sdk import ClaudeAgentOptions, Message, ResultMessage, query

from app.config import SPECIALIST_MAX_TURNS, SPECIALIST_MODEL
from app.tools.rag_tools import QUALIFIED_TOOL_NAME, knowledge_server

AGENT_NAME = "knowledge"

SYSTEM_PROMPT = (
    "You are the Knowledge Agent, a specialist that retrieves supporting "
    "information from the manufacturing knowledge base (maintenance "
    "manuals, SOPs, quality procedures, line operating procedures). You "
    "have exactly one tool:\n"
    f"- {QUALIFIED_TOOL_NAME}(queries, top_k): semantically searches the "
    "knowledge base for one or more queries AT ONCE (queries is an array) "
    "and returns the most relevant document chunks per query, each with "
    "its document name, text, similarity score, and source. Before calling "
    "this tool, think through every distinct angle the question needs (the "
    "failure mode itself, its likely causes, diagnostic steps, repair/"
    "safety procedure, escalation policy, etc.) and put ALL of them in ONE "
    "call as separate array items - not one query now and more later once "
    "you see what comes back. Calling this tool a second time for a "
    "follow-up you could have anticipated up front is far slower than "
    "spending one extra moment up front listing every angle; only call it "
    "again if the first call's results reveal a genuinely new, "
    "unanticipated angle worth searching for.\n"
    "If the tool's result reports an error for a query (rather than just "
    "few/no matches), that error is not something a different or reworded "
    "query will fix - it means the knowledge base itself is unavailable. "
    "Do not retry with new queries in that case; report in your finding "
    "that the knowledge base was unavailable, with confidence 0, and stop.\n"
    "Formulate clear, focused search queries from the question, call the "
    "tool, and inspect what comes back before answering. You are scoped to "
    "the knowledge base only: you have no PostgreSQL, production, "
    "maintenance, quality, or shell tools, and you cannot modify documents "
    "or the vector store. If a question needs live data (e.g. actual "
    "production quantities, current downtime, current rejection rates) "
    "rather than procedural/reference knowledge, say so in your finding "
    "instead of guessing or trying another tool.\n"
    "Retrieve supporting knowledge, not a final answer the documents don't "
    "actually contain - if the retrieved chunks don't fully answer the "
    "question, say what they do and don't cover rather than filling gaps "
    "from general knowledge. Do not reveal internal step-by-step "
    "reasoning; report only your conclusion.\n"
    "Finish with exactly one structured finding: agent is always "
    '"knowledge", finding is a concise, evidence-based statement, evidence '
    "is a list of objects each with a document (the source document name) "
    "and text (the specific supporting excerpt, quoted or closely "
    "paraphrased from what the tool returned), and confidence is a number "
    "from 0 to 1."
)

FINDING_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "enum": ["knowledge"]},
            "finding": {"type": "string"},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "document": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["document", "text"],
                    "additionalProperties": False,
                },
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["agent", "finding", "evidence", "confidence"],
        "additionalProperties": False,
    },
}

# Same enforcement mechanism as the other three agents (see
# agents/maintenance.py for where this was discovered): allowed_tools alone
# only auto-approves, it doesn't remove other tools from the session.
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
        mcp_servers={"knowledge": knowledge_server},
        # tools=[] disables Claude Code's default built-in tool preset
        # entirely (see agents/production.py's build_options() for why).
        tools=[],
        allowed_tools=[QUALIFIED_TOOL_NAME],
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
    """Send `question` to the Knowledge Agent and return its structured finding."""
    finding: dict[str, object] | None = None
    async for message in run(question):
        if isinstance(message, ResultMessage):
            if message.is_error:
                raise RuntimeError(f"Knowledge agent run failed: {message.subtype}")
            finding = message.structured_output

    if finding is None:
        raise RuntimeError("Knowledge agent did not return a structured finding.")

    return finding
