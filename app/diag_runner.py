"""Controlled resource-ceiling experiments (Render vs local investigation -
see the accompanying report). Runs one of three fixed scenarios, each in
its own OS process, so the exact subprocess count/timing Render measures
for a scenario can be compared directly against the same scenario run
locally:

    knowledge             Knowledge Agent alone - no Supervisor subprocess,
                           no Production/Maintenance/Quality subprocess.
                           Isolates whatever is unique to Knowledge (the
                           fastembed ONNX embedding model + Qdrant call)
                           from "two Agent SDK subprocesses running at once".

    supervisor-other       Supervisor delegates to a non-Knowledge
                           specialist (production) - two Agent SDK
                           subprocesses concurrently, but Qdrant/embedding
                           work never happens. Isolates general
                           Supervisor+specialist concurrency from anything
                           Knowledge-specific.

    supervisor-knowledge   Supervisor delegates specifically to Knowledge -
                           the scenario that reportedly fails on Render:
                           two Agent SDK subprocesses concurrently, one of
                           which also does the Qdrant/embedding work.

Each default question below is deliberately the one from
app/e2e_report.py's own live scenarios 2 and 5 (already verified there to
route to exactly one specialist), so a mis-route is a visible surprise, not
an assumption.

Two ways to run this:

  1. CLI (local dev, or anywhere with a shell):
         python -m app.diag_runner knowledge
         python -m app.diag_runner supervisor-other
         python -m app.diag_runner supervisor-knowledge

  2. HTTP, admin-only (Render's free plan has no Shell/SSH - see
     app/server.py's /api/admin/diag/{mode} endpoint, which calls
     run_experiment() below over a request instead): GET
     /api/admin/diag/knowledge etc. while logged in as an admin. Response
     JSON carries the same fields printed here; the "[diag] ..." lines
     still print to stdout either way, so they land in Render's log
     stream same as any other request.

Either way, prints/returns the same "[diag] ..." lines app/diagnostics.py
emits at every subprocess boundary, plus a final snapshot summary table and
total wall-clock time. A custom question can replace the CLI default:
`python -m app.diag_runner knowledge "..."`.
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

from app import diagnostics
from app.agents.knowledge import investigate as investigate_knowledge
from app.agents.supervisor import investigate_with_trace
from app.config import require_api_key

MODES = {
    "knowledge": "What does the motor failure SOP recommend?",
    "supervisor-other": "How much did Line 4 produce yesterday?",
    "supervisor-knowledge": "What does the motor failure SOP recommend?",
}


async def run_experiment(mode: str, question: str | None = None) -> dict[str, Any]:
    """Run one named scenario and return a JSON-safe summary. Shared by the
    CLI entry point below and app/server.py's admin-only diagnostic
    endpoint - the only way to trigger these on a Render free instance,
    which has no Shell/SSH access.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {list(MODES)}, got {mode!r}")
    question = question or MODES[mode]

    diagnostics.snapshot(f"diag_runner:{mode}:process_baseline")
    start = time.monotonic()
    result: dict[str, Any] = {"mode": mode, "question": question}
    try:
        if mode == "knowledge":
            finding = await investigate_knowledge(question)
            result["finding"] = finding
        else:
            report, trace = await investigate_with_trace(question)
            result["agents_selected"] = list(trace.selected_agents)
            result["tools_used"] = [c.tool for c in trace.tool_calls]
            result["root_cause"] = report.get("root_cause")
        result["success"] = True
    except Exception as exc:  # noqa: BLE001 - this is a diagnostic probe, report the failure, don't hide it
        result["success"] = False
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - start, 2)
        result["snapshots"] = diagnostics.all_snapshots()

    status = "SUCCEEDED" if result["success"] else "FAILED"
    print(f"\n[diag_runner] {mode} {status} in {result['elapsed_seconds']}s")
    if not result["success"]:
        print(f"[diag_runner] {result['error_type']}: {result['error']}")
    print("\n[diag_runner] snapshot summary:")
    print(diagnostics.summarize())

    return result


def main() -> None:
    require_api_key()
    if len(sys.argv) < 2 or sys.argv[1] not in MODES:
        print(f"Usage: python -m app.diag_runner <{'|'.join(MODES)}> [custom question]")
        sys.exit(1)

    mode = sys.argv[1]
    question = " ".join(sys.argv[2:]) or None
    print(f"[diag_runner] mode={mode} question={question or MODES[mode]!r}\n")
    result = asyncio.run(run_experiment(mode, question))
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
