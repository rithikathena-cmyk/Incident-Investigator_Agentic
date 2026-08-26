"""Quality tools (Step 6): get_quality_metrics, get_defect_distribution,
compare_quality_history.

Bridge between the Claude Agent SDK and the database only - all SQL/ORM
code lives in app.database.quality_repository. The Quality Agent never
touches SQL directly:
Quality Agent -> Tool (this file) -> Database (quality_repository.py) ->
Tool result -> Quality Agent.

Step 9: every call is gated by the deterministic capability layer
(app.guardrails.capabilities) BEFORE any database work happens.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from app.database import quality_repository as repo
from app.guardrails import capabilities

OWNING_AGENT = "quality"
QUALITY_SERVER_NAME = "quality"

GET_QUALITY_METRICS = "get_quality_metrics"
GET_QUALITY_METRICS_BATCH = "get_quality_metrics_batch"
GET_DEFECT_DISTRIBUTION = "get_defect_distribution"
COMPARE_QUALITY_HISTORY = "compare_quality_history"

# How Claude addresses these tools once registered via mcp_servers=.
QUALIFIED_TOOL_NAMES = {
    GET_QUALITY_METRICS: f"mcp__{QUALITY_SERVER_NAME}__{GET_QUALITY_METRICS}",
    GET_QUALITY_METRICS_BATCH: f"mcp__{QUALITY_SERVER_NAME}__{GET_QUALITY_METRICS_BATCH}",
    GET_DEFECT_DISTRIBUTION: f"mcp__{QUALITY_SERVER_NAME}__{GET_DEFECT_DISTRIBUTION}",
    COMPARE_QUALITY_HISTORY: f"mcp__{QUALITY_SERVER_NAME}__{COMPARE_QUALITY_HISTORY}",
}

# Same reasoning as tools/production_tools.py's get_production_metrics_batch:
# get_quality_metrics only takes one line/date pair, so a question that
# doesn't name an exact date (e.g. "why did rejection increase?", with no
# date given) gets answered by checking candidate dates one at a time, each
# its own LLM round trip - observed empirically (a live run of exactly that
# kind of question made 7 sequential get_quality_metrics calls). This batch
# tool collapses that into ONE call; lookups still run concurrently
# (bounded) via asyncio.to_thread + a semaphore.
_MAX_CONCURRENT_LOOKUPS = 5


def _not_found(line_id: str) -> dict[str, Any]:
    normalized = repo.normalize_line_id(line_id)
    return {
        "content": [{"type": "text", "text": f"No production line found with id {normalized}."}],
        "is_error": True,
    }


def _invalid(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "is_error": True}


def _denied(decision: capabilities.CapabilityDecision) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(decision.to_dict())}], "is_error": True}


@tool(
    GET_QUALITY_METRICS,
    "Look up quality inspection results for one production line on one "
    "date: inspected quantity, rejected quantity, rejection percentage, "
    "and a summary of defect types. Use this for 'was there a quality "
    "problem' style questions.",
    {
        "line_id": Annotated[str, "Production line identifier, e.g. 'Line 4' or 'LINE-4'"],
        "date": Annotated[str, "Date in YYYY-MM-DD format, e.g. '2026-08-25'"],
    },
)
async def get_quality_metrics(args: dict[str, Any]) -> dict[str, Any]:
    decision = capabilities.authorize(OWNING_AGENT, GET_QUALITY_METRICS)
    if not decision.allowed:
        return _denied(decision)

    line_id = str(args["line_id"])
    date = str(args["date"])

    result = await asyncio.to_thread(repo.get_quality_metrics, line_id, date)
    if result is None:
        return _not_found(line_id)
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}


_BATCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "requests": {
            "type": "array",
            "description": (
                "One or more {line_id, date} pairs. Pass every pair you want "
                "in this ONE call - they run concurrently, which is far "
                "faster than calling get_quality_metrics once per pair and "
                "waiting for each before starting the next."
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
        result = await asyncio.to_thread(repo.get_quality_metrics, line_id, date)

    if result is None:
        normalized = repo.normalize_line_id(line_id)
        return {"line_id": normalized, "date": date, "error": f"No quality data for {normalized} on {date}."}
    return result


@tool(
    GET_QUALITY_METRICS_BATCH,
    "Look up quality inspection results for SEVERAL {line_id, date} pairs "
    "at once - same data as get_quality_metrics, but for every pair in one "
    "call instead of one call per pair. Use this whenever a question "
    "doesn't name one exact date (e.g. scanning a few candidate dates to "
    "find when rejection changed) or needs more than one line.",
    _BATCH_INPUT_SCHEMA,
)
async def get_quality_metrics_batch(args: dict[str, Any]) -> dict[str, Any]:
    decision = capabilities.authorize(OWNING_AGENT, GET_QUALITY_METRICS_BATCH)
    if not decision.allowed:
        return _denied(decision)

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


@tool(
    GET_DEFECT_DISTRIBUTION,
    "Look up the breakdown of defect types for one production line on one "
    "date: each defect type's count and its percentage of that day's total "
    "rejects. Use this for 'what was the main/dominant defect' style "
    "questions.",
    {
        "line_id": Annotated[str, "Production line identifier, e.g. 'Line 4' or 'LINE-4'"],
        "date": Annotated[str, "Date in YYYY-MM-DD format, e.g. '2026-08-25'"],
    },
)
async def get_defect_distribution(args: dict[str, Any]) -> dict[str, Any]:
    decision = capabilities.authorize(OWNING_AGENT, GET_DEFECT_DISTRIBUTION)
    if not decision.allowed:
        return _denied(decision)

    line_id = str(args["line_id"])
    date = str(args["date"])

    result = await asyncio.to_thread(repo.get_defect_distribution, line_id, date)
    if result is None:
        return _not_found(line_id)
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}


@tool(
    COMPARE_QUALITY_HISTORY,
    "Compare one production line's rejection rate on one date against its "
    "historical average over the preceding `lookback_days` days: current "
    "rate, historical average, the difference, and a trend label. Use this "
    "for 'was quality worse than normal / usual' style questions.",
    {
        "line_id": Annotated[str, "Production line identifier, e.g. 'Line 4' or 'LINE-4'"],
        "date": Annotated[str, "Date in YYYY-MM-DD format, e.g. '2026-08-25'"],
        "lookback_days": Annotated[int, "How many days of history to compare against, e.g. 14"],
    },
)
async def compare_quality_history(args: dict[str, Any]) -> dict[str, Any]:
    decision = capabilities.authorize(OWNING_AGENT, COMPARE_QUALITY_HISTORY)
    if not decision.allowed:
        return _denied(decision)

    line_id = str(args["line_id"])
    date = str(args["date"])

    try:
        lookback_days = int(args["lookback_days"])
    except (TypeError, ValueError):
        return _invalid(f"lookback_days must be an integer, got {args['lookback_days']!r}.")
    if lookback_days <= 0:
        return _invalid("lookback_days must be a positive integer.")

    result = await asyncio.to_thread(repo.compare_quality_history, line_id, date, lookback_days)
    if result is None:
        return _not_found(line_id)
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}


quality_server = create_sdk_mcp_server(
    name=QUALITY_SERVER_NAME,
    version="2.0.0",
    tools=[get_quality_metrics, get_quality_metrics_batch, get_defect_distribution, compare_quality_history],
)
