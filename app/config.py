"""Environment configuration for the Manufacturing Investigator agent.

The Claude Agent SDK reads ANTHROPIC_API_KEY directly from the process
environment when it launches the Claude Code engine as a subprocess - it does
not load .env files and has no `api_key=` parameter to pass a key through
code. ANTHROPIC_API_KEY is the documented, supported auth method for
anything other than solo local development (CI, other machines, anything
resembling a product). If it's unset, the underlying engine can still
authenticate using an existing local `claude` CLI login on this machine, so
we warn instead of blocking - this keeps local learning/dev unblocked without
silently hiding the fact that the recommended env var isn't set.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

# Same idempotent pattern as app/database/connection.py and app/rag/ingest.py
# - called here too (not just relied on via import order) because this
# module's env reads happen at import time, and this can be the first
# module of the three imported.
load_dotenv()

REQUIRED_ENV_VARS = ("ANTHROPIC_API_KEY",)

# Model tiering (performance): with no override, every agent session in this
# project fell back to the CLI's own default model - measured (see the
# perf section of README/the optimization report) to be a large/slow
# reasoning-tier model (claude-opus-5), which is serious overkill for
# specialist sessions whose own job is just "pick the right tool(s) from a
# handful of options and summarize a small JSON result": most of the
# ~4.5-minute baseline was this model's per-turn generation latency, not
# database/Qdrant time or LLM-turn count (those were already batched).
# Switching the four specialists to a fast model, while keeping the
# Supervisor's own cross-specialist synthesis on a mid-tier model, is what
# actually moved the needle - re-measure with python -m app.e2e_report or
# the timing harness before changing these further; do not tune blindly.
SPECIALIST_MODEL = os.environ.get("SPECIALIST_MODEL", "haiku")
SUPERVISOR_MODEL = os.environ.get("SUPERVISOR_MODEL", "sonnet")

# Turn caps (performance + safety): bounds how many reasoning/tool-call
# round trips a single agent session may take before the SDK stops it,
# without capping so tightly that a legitimate multi-tool question gets cut
# off mid-investigation. A specialist's normal path is one domain tool call
# -> structured output (2 turns); this leaves headroom for a second/third
# tool call (e.g. Quality checking more than one of its tools) while still
# bounding a runaway one-call-per-item loop.
SPECIALIST_MAX_TURNS = int(os.environ.get("SPECIALIST_MAX_TURNS", "8"))
# The Supervisor's own path is plan -> delegate -> (optional follow-up
# delegate) -> final structured synthesis (up to 4 turns). Measured one
# transient error_max_structured_output_retries failure at max_turns=6
# (did not reproduce on retry - not clearly caused by the cap, but cheap
# to give more headroom against exactly this kind of failure, per "do not
# arbitrarily force a low limit that causes incomplete answers").
SUPERVISOR_MAX_TURNS = int(os.environ.get("SUPERVISOR_MAX_TURNS", "8"))


def require_api_key() -> None:
    """Warn if ANTHROPIC_API_KEY is not set in the environment."""
    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        print(
            "[investigator] Warning: "
            f"{', '.join(missing)} not set in the environment.\n"
            "  This falls back to an existing local `claude` CLI login, if "
            "any. For anything beyond solo local use, set it explicitly:\n"
            "    macOS/Linux:  export ANTHROPIC_API_KEY=sk-ant-...\n"
            '    PowerShell:   $env:ANTHROPIC_API_KEY = "sk-ant-..."'
        )
