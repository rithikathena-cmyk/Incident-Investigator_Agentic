"""Maintenance tools (Step 5): get_machine_downtime, get_maintenance_events,
get_machine_history.

Bridge between the Claude Agent SDK and the database only - all SQL/ORM
code lives in app.database.maintenance_repository. The Maintenance Agent
never touches SQL directly:
Maintenance Agent -> Tool (this file) -> Database (maintenance_repository.py)
-> Tool result -> Maintenance Agent.

Step 9: every call is gated by the deterministic capability layer
(app.guardrails.capabilities) BEFORE any database work happens.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from app.database import maintenance_repository as repo
from app.guardrails import capabilities

OWNING_AGENT = "maintenance"
MAINTENANCE_SERVER_NAME = "maintenance"

GET_MACHINE_DOWNTIME = "get_machine_downtime"
GET_LINE_DOWNTIME = "get_line_downtime"
GET_LINE_DOWNTIME_BATCH = "get_line_downtime_batch"
GET_MAINTENANCE_EVENTS = "get_maintenance_events"
GET_MACHINE_HISTORY = "get_machine_history"

# How Claude addresses these tools once registered via mcp_servers=.
QUALIFIED_TOOL_NAMES = {
    GET_MACHINE_DOWNTIME: f"mcp__{MAINTENANCE_SERVER_NAME}__{GET_MACHINE_DOWNTIME}",
    GET_LINE_DOWNTIME: f"mcp__{MAINTENANCE_SERVER_NAME}__{GET_LINE_DOWNTIME}",
    GET_LINE_DOWNTIME_BATCH: f"mcp__{MAINTENANCE_SERVER_NAME}__{GET_LINE_DOWNTIME_BATCH}",
    GET_MAINTENANCE_EVENTS: f"mcp__{MAINTENANCE_SERVER_NAME}__{GET_MAINTENANCE_EVENTS}",
    GET_MACHINE_HISTORY: f"mcp__{MAINTENANCE_SERVER_NAME}__{GET_MACHINE_HISTORY}",
}

# Same reasoning as tools/production_tools.py's get_production_metrics_batch
# and tools/quality_tools.py's get_quality_metrics_batch: get_line_downtime
# only takes one line/date pair, so a question that doesn't name one exact
# date gets answered by scanning candidate dates one at a time, each its
# own LLM round trip - observed empirically (a live run of an ambiguous-date
# question made 6 sequential get_line_downtime calls). This batch tool
# collapses that into ONE call; lookups still run concurrently (bounded)
# via asyncio.to_thread + a semaphore.
_MAX_CONCURRENT_LOOKUPS = 5


def _not_found(machine_id: str) -> dict[str, Any]:
    normalized = repo.normalize_machine_id(machine_id)
    return {
        "content": [{"type": "text", "text": f"No machine found with id {normalized}."}],
        "is_error": True,
    }


def _denied(decision: capabilities.CapabilityDecision) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(decision.to_dict())}], "is_error": True}


@tool(
    GET_MACHINE_DOWNTIME,
    "Look up maintenance downtime and events for one machine on one date, "
    "including which shift appears most affected (from production records). "
    "Use this for 'what happened to machine X on date Y' style questions.",
    {
        "machine_id": Annotated[str, "Machine identifier, e.g. 'M-104'"],
        "date": Annotated[str, "Date in YYYY-MM-DD format, e.g. '2026-08-25'"],
    },
)
async def get_machine_downtime(args: dict[str, Any]) -> dict[str, Any]:
    decision = capabilities.authorize(OWNING_AGENT, GET_MACHINE_DOWNTIME)
    if not decision.allowed:
        return _denied(decision)

    machine_id = str(args["machine_id"])
    date = str(args["date"])

    result = await asyncio.to_thread(repo.get_machine_downtime, machine_id, date)
    if result is None:
        return _not_found(machine_id)
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}


@tool(
    GET_LINE_DOWNTIME,
    "Look up maintenance downtime and events for EVERY machine on one "
    "production line on one date, in a single call, sorted worst-downtime-"
    "first. Use this - not repeated get_machine_downtime calls - whenever "
    "a question names a line but not a specific machine (e.g. 'what went "
    "down on Line 4 yesterday'): it answers 'which machine' directly "
    "instead of checking machines one at a time.",
    {
        "line_id": Annotated[str, "Line identifier, e.g. 'Line 4' or 'LINE-4'"],
        "date": Annotated[str, "Date in YYYY-MM-DD format, e.g. '2026-08-25'"],
    },
)
async def get_line_downtime(args: dict[str, Any]) -> dict[str, Any]:
    decision = capabilities.authorize(OWNING_AGENT, GET_LINE_DOWNTIME)
    if not decision.allowed:
        return _denied(decision)

    line_id = str(args["line_id"])
    date = str(args["date"])

    result = await asyncio.to_thread(repo.get_line_downtime, line_id, date)
    if result is None:
        return {"content": [{"type": "text", "text": f"No line found with id {line_id}."}], "is_error": True}
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}


_LINE_BATCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "requests": {
            "type": "array",
            "description": (
                "One or more {line_id, date} pairs. Pass every pair you want "
                "in this ONE call - they run concurrently, which is far "
                "faster than calling get_line_downtime once per pair and "
                "waiting for each before starting the next."
            ),
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "line_id": {"type": "string", "description": "Line identifier, e.g. 'Line 4' or 'LINE-4'"},
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


async def _line_lookup_one(line_id: str, date: str, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    async with semaphore:
        result = await asyncio.to_thread(repo.get_line_downtime, line_id, date)

    if result is None:
        return {"line_id": line_id, "date": date, "error": f"No line found with id {line_id}."}
    return result


@tool(
    GET_LINE_DOWNTIME_BATCH,
    "Look up maintenance downtime for SEVERAL {line_id, date} pairs at "
    "once - same data as get_line_downtime, but for every pair in one call "
    "instead of one call per pair. Use this whenever the question doesn't "
    "name one exact date (e.g. scanning a handful of candidate dates to "
    "find when a failure started) or needs more than one line.",
    _LINE_BATCH_INPUT_SCHEMA,
)
async def get_line_downtime_batch(args: dict[str, Any]) -> dict[str, Any]:
    decision = capabilities.authorize(OWNING_AGENT, GET_LINE_DOWNTIME_BATCH)
    if not decision.allowed:
        return _denied(decision)

    requests = args.get("requests") or []
    if not requests:
        return {"content": [{"type": "text", "text": "requests must contain at least one item."}], "is_error": True}

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_LOOKUPS)
    results = await asyncio.gather(
        *(_line_lookup_one(str(r["line_id"]), str(r["date"]), semaphore) for r in requests)
    )

    all_failed = all("error" in r for r in results)
    return {
        "content": [{"type": "text", "text": json.dumps({"results": results}, indent=2, default=str)}],
        "is_error": all_failed,
    }


@tool(
    GET_MAINTENANCE_EVENTS,
    "Look up maintenance events for one machine over a date range: event "
    "types, failure types, downtime, and descriptions. Use this to see what "
    "maintenance activity happened on a machine over a period of time.",
    {
        "machine_id": Annotated[str, "Machine identifier, e.g. 'M-104'"],
        "start_date": Annotated[str, "Start of the range, YYYY-MM-DD, inclusive"],
        "end_date": Annotated[str, "End of the range, YYYY-MM-DD, inclusive"],
    },
)
async def get_maintenance_events(args: dict[str, Any]) -> dict[str, Any]:
    decision = capabilities.authorize(OWNING_AGENT, GET_MAINTENANCE_EVENTS)
    if not decision.allowed:
        return _denied(decision)

    machine_id = str(args["machine_id"])
    start_date = str(args["start_date"])
    end_date = str(args["end_date"])

    result = await asyncio.to_thread(repo.get_maintenance_events, machine_id, start_date, end_date)
    if result is None:
        return _not_found(machine_id)
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}


@tool(
    GET_MACHINE_HISTORY,
    "Look up a machine's full maintenance history: every recorded event, "
    "total downtime, and how often each failure type has recurred. Use "
    "this for 'has this machine had similar problems before' style "
    "questions.",
    {"machine_id": Annotated[str, "Machine identifier, e.g. 'M-104'"]},
)
async def get_machine_history(args: dict[str, Any]) -> dict[str, Any]:
    decision = capabilities.authorize(OWNING_AGENT, GET_MACHINE_HISTORY)
    if not decision.allowed:
        return _denied(decision)

    machine_id = str(args["machine_id"])

    result = await asyncio.to_thread(repo.get_machine_history, machine_id)
    if result is None:
        return _not_found(machine_id)
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}


maintenance_server = create_sdk_mcp_server(
    name=MAINTENANCE_SERVER_NAME,
    version="1.2.0",
    tools=[
        get_machine_downtime,
        get_line_downtime,
        get_line_downtime_batch,
        get_maintenance_events,
        get_machine_history,
    ],
)
