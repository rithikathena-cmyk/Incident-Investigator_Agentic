"""Custom tool: get_production_metrics (Step 4: now backed by PostgreSQL).

This module is the bridge between the Claude Agent SDK and the database. It
contains no SQL/ORM code itself - all of that lives in
app.database.production_repository, which is the only module that talks to
the database. agents/production.py never touches SQL or the repository
directly either:
Agent -> Tool (this file) -> Database (production_repository.py) -> Tool result -> Agent.

Step 9: every call is gated by the deterministic capability layer
(app.guardrails.capabilities) BEFORE any database work happens - not by the
prompt, and not by an SDK permission flag.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from app.database import production_repository as repository
from app.guardrails import capabilities

OWNING_AGENT = "production"
PRODUCTION_SERVER_NAME = "production"
PRODUCTION_TOOL_NAME = "get_production_metrics"
PRODUCTION_BATCH_TOOL_NAME = "get_production_metrics_batch"
# How Claude addresses these tools once registered via mcp_servers=.
QUALIFIED_TOOL_NAME = f"mcp__{PRODUCTION_SERVER_NAME}__{PRODUCTION_TOOL_NAME}"
QUALIFIED_BATCH_TOOL_NAME = f"mcp__{PRODUCTION_SERVER_NAME}__{PRODUCTION_BATCH_TOOL_NAME}"

# Same reasoning as tools/rag_tools.py's search_manufacturing_knowledge and
# tools/maintenance_tools.py's get_line_downtime: with only a single-item
# tool, a question that needs more than one line/date pair (e.g. "compare
# the incident date against the prior week") gets answered with repeated
# get_production_metrics calls, one per pair, each its own LLM reasoning
# round trip - observed empirically (a live run asked one line/date
# question and still made 5 sequential get_production_metrics calls).
# get_production_metrics_batch collapses that into ONE tool call; the
# underlying Postgres lookups still run concurrently (bounded, not
# fire-them-all-at-once) via asyncio.to_thread + a semaphore.
_MAX_CONCURRENT_LOOKUPS = 5


@tool(
    PRODUCTION_TOOL_NAME,
    "Look up real production performance for one manufacturing line on one "
    "date from the PostgreSQL production database: planned vs. actual "
    "quantity, production loss percentage, downtime minutes, and a "
    "per-shift breakdown (including per-machine detail within each shift). "
    "Use this whenever a question needs actual production numbers for a "
    "specific line and date (e.g. investigating a production drop). Do not "
    "use it for questions unrelated to manufacturing production data.",
    {
        "line_id": Annotated[str, "Production line identifier, e.g. 'Line 4' or 'LINE-4'"],
        "date": Annotated[str, "Date in YYYY-MM-DD format, e.g. '2026-08-25'"],
    },
)
async def get_production_metrics(args: dict[str, Any]) -> dict[str, Any]:
    decision = capabilities.authorize(OWNING_AGENT, PRODUCTION_TOOL_NAME)
    if not decision.allowed:
        return {"content": [{"type": "text", "text": json.dumps(decision.to_dict())}], "is_error": True}

    line_id = str(args["line_id"])
    date = str(args["date"])

    # repository.get_production_metrics() is a blocking (sync) DB call -
    # run it off the event loop so the agent loop isn't blocked on it.
    metrics = await asyncio.to_thread(repository.get_production_metrics, line_id, date)
    if metrics is None:
        normalized = repository.normalize_line_id(line_id)
        available = await asyncio.to_thread(repository.available_dates, normalized)
        message = (
            f"No production data for {normalized} on {date}. "
            f"Known dates for {normalized}: {', '.join(available) or 'none'}."
        )
        return {"content": [{"type": "text", "text": message}], "is_error": True}

    return {"content": [{"type": "text", "text": json.dumps(metrics, indent=2, default=str)}]}


_BATCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "requests": {
            "type": "array",
            "description": (
                "One or more {line_id, date} pairs. Pass every pair you want "
                "in this ONE call - they run concurrently, which is far "
                "faster than calling get_production_metrics once per pair "
                "and waiting for each before starting the next."
            ),
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "line_id": {"type": "string", "description": "Production line identifier, e.g. 'Line 4' or 'LINE-4'"},
                    "date": {"type": "string", "description": "Date in YYYY-MM-DD format, e.g. '2026-08-25'"},
                },
                "required": ["line_id", "date"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["requests"],
    "additionalProperties": False,
}


async def _lookup_one(line_id: str, date: str, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    async with semaphore:
        metrics = await asyncio.to_thread(repository.get_production_metrics, line_id, date)

    if metrics is None:
        normalized = repository.normalize_line_id(line_id)
        return {"line_id": normalized, "date": date, "error": f"No production data for {normalized} on {date}."}
    return metrics


@tool(
    PRODUCTION_BATCH_TOOL_NAME,
    "Look up real production performance for SEVERAL {line_id, date} pairs "
    "at once - same data as get_production_metrics, but for every pair in "
    "one call instead of one call per pair. Use this whenever a question "
    "needs more than one line and/or more than one date (e.g. comparing an "
    "incident date against several prior days).",
    _BATCH_INPUT_SCHEMA,
)
async def get_production_metrics_batch(args: dict[str, Any]) -> dict[str, Any]:
    decision = capabilities.authorize(OWNING_AGENT, PRODUCTION_BATCH_TOOL_NAME)
    if not decision.allowed:
        return {"content": [{"type": "text", "text": json.dumps(decision.to_dict())}], "is_error": True}

    requests = args.get("requests") or []
    if not requests:
        return {"content": [{"type": "text", "text": "requests must contain at least one item."}], "is_error": True}

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_LOOKUPS)
    results = await asyncio.gather(
        *(_lookup_one(str(r["line_id"]), str(r["date"]), semaphore) for r in requests)
    )

    all_failed = all("error" in r for r in results)
    return {
        "content": [{"type": "text", "text": json.dumps({"results": results}, indent=2, default=str)}],
        "is_error": all_failed,
    }


production_server = create_sdk_mcp_server(
    name=PRODUCTION_SERVER_NAME,
    version="3.0.0",
    tools=[get_production_metrics, get_production_metrics_batch],
)
