"""Knowledge tool (Step 7): search_manufacturing_knowledge.

Bridge between the Claude Agent SDK and the RAG pipeline only - all
embedding/Qdrant code lives in app.rag.*, which has no Claude Agent SDK
dependency at all. The Knowledge Agent never touches Qdrant directly:
Knowledge Agent -> Tool (this file) -> RAG pipeline (app.rag.*)
-> Qdrant -> Tool result -> Knowledge Agent.

Step 9: every call is gated by the deterministic capability layer
(app.guardrails.capabilities) BEFORE any Qdrant work happens.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from app.guardrails import capabilities
from app.rag.search import search

OWNING_AGENT = "knowledge"
KNOWLEDGE_SERVER_NAME = "knowledge"

SEARCH_MANUFACTURING_KNOWLEDGE = "search_manufacturing_knowledge"

QUALIFIED_TOOL_NAME = f"mcp__{KNOWLEDGE_SERVER_NAME}__{SEARCH_MANUFACTURING_KNOWLEDGE}"

DEFAULT_TOP_K = 5

# Takes an ARRAY of queries, run concurrently - same "one tool, array
# input, real concurrency" pattern as tools/supervisor_tools.py's
# delegate_to_specialists, for the same reason: with a single-query tool,
# the Knowledge Agent called it once per angle it wanted to search, one at
# a time across separate turns (observed: up to 8 sequential calls, each a
# full embed+search+reasoning round trip). A JSON Schema dict (not the
# @tool shorthand) is used here because the shorthand doesn't express
# "array of strings".
#
# Concurrency is capped (not just fire-them-all-at-once): app.rag.search
# reuses one process-wide QdrantClient (app.rag.ingest.get_client()), and
# firing every query at it simultaneously from separate threads (one query
# count observed: 9) exceeded what its connection pool could hold, which
# aborted every in-flight connection - one slow/bad query would otherwise
# have taken the whole batch down with it. A semaphore keeps a handful of
# searches in flight at once (still far faster than fully sequential) and
# each query's failure is caught individually so it can't sink the others.
_MAX_CONCURRENT_SEARCHES = 4
_SEARCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "description": (
                "One or more natural-language search queries. Pass every "
                "distinct angle you want to search for in this ONE call - "
                "they all run concurrently, which is far faster than "
                "calling this tool once per query and waiting for each "
                "before starting the next."
            ),
            "minItems": 1,
            "items": {"type": "string"},
        },
        "top_k": {
            "type": "integer",
            "description": "Number of results to return per query (default 5).",
        },
    },
    "required": ["queries"],
    "additionalProperties": False,
}


async def _search_one(query: str, top_k: int, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    async with semaphore:
        try:
            results = await asyncio.to_thread(search, query, top_k=top_k)
        except Exception as exc:  # noqa: BLE001 - one query's failure must not sink the whole batch
            return {"query": query, "results": [], "error": str(exc)}

    return {
        "query": query,
        "results": [
            {
                "document": r.document_name,
                "text": r.text,
                "similarity_score": round(r.score, 4),
                "source": r.document_path,
                "section": r.section_title,
            }
            for r in results
        ],
    }


@tool(
    SEARCH_MANUFACTURING_KNOWLEDGE,
    "Semantically search the manufacturing knowledge base (maintenance "
    "manuals, SOPs, quality procedures, line operating procedures) for "
    "chunks relevant to one or more queries at once. Returns the document "
    "name, the matching text, a similarity score, and source information "
    "for each result, grouped by query. Use this for any question about "
    "procedures, SOPs, or how-to guidance - not for live "
    "production/maintenance/quality data.",
    _SEARCH_INPUT_SCHEMA,
)
async def search_manufacturing_knowledge(args: dict[str, Any]) -> dict[str, Any]:
    decision = capabilities.authorize(OWNING_AGENT, SEARCH_MANUFACTURING_KNOWLEDGE)
    if not decision.allowed:
        return {"content": [{"type": "text", "text": json.dumps(decision.to_dict())}], "is_error": True}

    queries = [str(q) for q in (args.get("queries") or []) if str(q).strip()]
    if not queries:
        return {"content": [{"type": "text", "text": "queries must contain at least one item."}], "is_error": True}

    top_k = int(args.get("top_k") or DEFAULT_TOP_K)
    if top_k <= 0:
        return {
            "content": [{"type": "text", "text": "top_k must be a positive integer."}],
            "is_error": True,
        }

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_SEARCHES)
    results_by_query = await asyncio.gather(*(_search_one(q, top_k, semaphore) for q in queries))

    all_failed = all(r.get("error") for r in results_by_query)
    return {
        "content": [{"type": "text", "text": json.dumps({"results_by_query": results_by_query}, indent=2)}],
        "is_error": all_failed,
    }


knowledge_server = create_sdk_mcp_server(
    name=KNOWLEDGE_SERVER_NAME,
    version="1.0.0",
    tools=[search_manufacturing_knowledge],
)
