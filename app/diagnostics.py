"""Diagnostic instrumentation for the "is Render resource-constrained?"
investigation. Not part of the product architecture - purely additive,
read-only observation logged around each Agent SDK subprocess boundary
(the Supervisor's own query() and each specialist's, especially Knowledge)
so a Render deployment and a local run can be compared on equal terms.

Nothing here changes what an agent does, what tools it can call, or how a
request is answered - only what gets printed to stdout, using the same
print()-based convention the rest of this project already uses (see
server.py/main.py's "[investigator]" prefix) so it shows up in Render's log
stream with zero extra plumbing.

Turn off entirely with DIAG_INSTRUMENTATION=0. On by default - each
snapshot is one psutil call plus a couple of /sys/fs/cgroup reads, cheap
enough not to perturb the timing being measured.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import psutil
from claude_agent_sdk import CLIConnectionError, CLIJSONDecodeError, CLINotFoundError, ProcessError

ENABLED = os.environ.get("DIAG_INSTRUMENTATION", "1") != "0"
_SAMPLE_INTERVAL_SECONDS = float(os.environ.get("DIAG_SAMPLE_INTERVAL", "3"))

_process = psutil.Process()
_process_start_monotonic = time.monotonic()
# psutil's interval-based cpu_percent() always returns 0.0 on its first
# call (nothing to compare against yet) - prime it now at import time so
# the first real snapshot() already has a meaningful delta.
_process.cpu_percent(interval=None)

_snapshots: list[dict[str, Any]] = []


def _read_first_line(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.readline().strip()
    except OSError:
        return None


def cgroup_memory_limit_bytes() -> int | None:
    """The container's actual memory ceiling (item 12), if this process is
    in a cgroup with one set - None on a host with no cgroup (plain
    Windows/macOS dev) or an unconstrained one. psutil.virtual_memory()
    does NOT reflect this: it reports total host memory even inside a
    tightly-capped container, which is exactly the number that would hide
    a Render-imposed ceiling.
    """
    raw = _read_first_line("/sys/fs/cgroup/memory.max")  # cgroup v2
    if raw is not None:
        if raw == "max":
            return None
        with contextlib.suppress(ValueError):
            return int(raw)

    raw = _read_first_line("/sys/fs/cgroup/memory/memory.limit_in_bytes")  # cgroup v1
    if raw is not None:
        with contextlib.suppress(ValueError):
            value = int(raw)
            return None if value > 2**62 else value  # v1's "no limit" sentinel
    return None


def cgroup_memory_current_bytes() -> int | None:
    raw = _read_first_line("/sys/fs/cgroup/memory.current")
    if raw is not None:
        with contextlib.suppress(ValueError):
            return int(raw)
    raw = _read_first_line("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    if raw is not None:
        with contextlib.suppress(ValueError):
            return int(raw)
    return None


def cgroup_cpu_limit_cores() -> float | None:
    """Effective CPU allocation from the cgroup quota (item 13) - e.g.
    Render's free plan grants a fraction of one core. None if unset/
    unlimited or not on a cgroup host at all.
    """
    raw = _read_first_line("/sys/fs/cgroup/cpu.max")  # cgroup v2: "$QUOTA $PERIOD"
    if raw is not None:
        parts = raw.split()
        if len(parts) == 2 and parts[0] != "max":
            with contextlib.suppress(ValueError, ZeroDivisionError):
                return int(parts[0]) / int(parts[1])
        return None

    quota = _read_first_line("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")  # cgroup v1
    period = _read_first_line("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    if quota is not None and period is not None:
        with contextlib.suppress(ValueError):
            q, p = int(quota), int(period)
            if q > 0 and p > 0:
                return q / p
    return None


def _mb(value_bytes: int | None) -> float | None:
    return round(value_bytes / 1_048_576, 1) if value_bytes is not None else None


def _children() -> list[dict[str, Any]]:
    out = []
    for child in _process.children(recursive=True):
        try:
            with child.oneshot():
                cpu_times = child.cpu_times()
                out.append(
                    {
                        "pid": child.pid,
                        "name": child.name(),
                        "status": child.status(),
                        "rss_mb": _mb(child.memory_info().rss),
                        "cpu_seconds": round(cpu_times.user + cpu_times.system, 2),
                        "age_seconds": round(time.time() - child.create_time(), 1),
                    }
                )
        except psutil.NoSuchProcess:
            continue  # exited between children() and oneshot() - not a bug, just a race
    return out


def snapshot(label: str) -> dict[str, Any]:
    """Record + print one point-in-time resource reading (items 1-4, 7, 8,
    12, 13). Safe to call at every subprocess boundary without perturbing
    the timing being measured.
    """
    if not ENABLED:
        return {}

    with _process.oneshot():
        rss_mb = _mb(_process.memory_info().rss)
        cpu_percent = _process.cpu_percent(interval=None)
        num_threads = _process.num_threads()

    children = _children()
    loadavg = os.getloadavg() if hasattr(os, "getloadavg") else None  # item 5/6: POSIX only (Render, not local Windows)

    record = {
        "label": label,
        "wall_time": datetime.now(timezone.utc).isoformat(),
        "elapsed_since_process_start_s": round(time.monotonic() - _process_start_monotonic, 2),
        "self_rss_mb": rss_mb,
        "self_cpu_percent": cpu_percent,
        "self_num_threads": num_threads,
        "logical_cpu_count": os.cpu_count(),
        "loadavg_1_5_15": loadavg,
        "cgroup_memory_limit_mb": _mb(cgroup_memory_limit_bytes()),
        "cgroup_memory_current_mb": _mb(cgroup_memory_current_bytes()),
        "cgroup_cpu_limit_cores": cgroup_cpu_limit_cores(),
        "child_process_count": len(children),
        "children": children,
    }
    _snapshots.append(record)

    child_summary = (
        ", ".join(f"{c['name']}(pid={c['pid']},rss={c['rss_mb']}MB,cpu={c['cpu_seconds']}s,age={c['age_seconds']}s)" for c in children)
        or "none"
    )
    print(
        f"[diag] {label} | t+{record['elapsed_since_process_start_s']}s | "
        f"self_rss={rss_mb}MB self_cpu%={cpu_percent} threads={num_threads} | "
        f"children={len(children)} [{child_summary}] | "
        f"cgroup_mem={record['cgroup_memory_current_mb']}/{record['cgroup_memory_limit_mb']}MB "
        f"cgroup_cpu_cores={record['cgroup_cpu_limit_cores']} logical_cpus={record['logical_cpu_count']} "
        f"loadavg={loadavg}",
        flush=True,
    )
    return record


def classify_failure(exc: BaseException, *, elapsed_s: float) -> str:
    """Best-effort classification of a subprocess-boundary failure into one
    of the buckets item 11 asks to distinguish (timeout / OOM / CPU
    starvation / process creation failure / SDK-CLI init failure / network
    failure / other). Heuristic, not authoritative - always printed
    alongside the raw exception (never hidden behind the label) so a human
    can override the call.
    """
    text = str(exc).lower()
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(exc, CLINotFoundError):
        return "process creation failure (CLI binary not found)"
    if isinstance(exc, MemoryError):
        return "OOM (Python-level MemoryError)"
    if isinstance(exc, OSError) and exc.errno in (12, 11):  # ENOMEM, EAGAIN
        return f"OOM / process creation failure (OS refused fork/exec - errno {exc.errno})"
    if isinstance(exc, ProcessError):
        if exc.exit_code in (137, -9):
            return "OOM (exit code/signal matches SIGKILL - the container OOM-killer's signature)"
        return f"SDK/CLI initialization failure (CLI exited with code {exc.exit_code})"
    if isinstance(exc, CLIJSONDecodeError):
        return "SDK/CLI initialization failure (malformed/truncated stdout from CLI)"
    if isinstance(exc, CLIConnectionError):
        return "process creation failure (subprocess spawn/connect failed)"
    if "econnrefused" in text or "network" in text or "dns" in text or "getaddrinfo" in text:
        return "network failure"
    if elapsed_s >= 60:
        return "timeout (no typed SDK exception, but this boundary ran >=60s - likely an external wrapper/proxy timeout, not the SDK's own)"
    return f"other ({type(exc).__name__}: {exc})"


@contextlib.asynccontextmanager
async def watch_subprocess(label: str) -> AsyncIterator[None]:
    """Wrap one Agent SDK query()/subprocess boundary (e.g. "supervisor" or
    "knowledge"): a snapshot immediately before, a periodic snapshot every
    DIAG_SAMPLE_INTERVAL seconds while it runs - item 6 ("CPU utilization
    while Knowledge subprocess initialization is timing out") needs samples
    taken *during* the hang, a before/after pair alone can't see that - and
    a final snapshot + exact duration + failure classification (items 9,
    10, 11) on the way out, success or failure either way.
    """
    if not ENABLED:
        yield
        return

    start = time.monotonic()
    snapshot(f"{label}:before")

    async def _sampler() -> None:
        while True:
            await asyncio.sleep(_SAMPLE_INTERVAL_SECONDS)
            snapshot(f"{label}:during(t+{round(time.monotonic() - start, 1)}s)")

    sampler_task = asyncio.create_task(_sampler())
    try:
        yield
    except BaseException as exc:
        elapsed = time.monotonic() - start
        classification = classify_failure(exc, elapsed_s=elapsed)
        snapshot(f"{label}:failed(after {round(elapsed, 2)}s)")
        print(
            f"[diag] {label} FAILED after {round(elapsed, 2)}s | classification={classification} | "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        raise
    else:
        elapsed = time.monotonic() - start
        snapshot(f"{label}:after(ok,{round(elapsed, 2)}s)")
        print(f"[diag] {label} OK in {round(elapsed, 2)}s", flush=True)
    finally:
        sampler_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sampler_task


async def time_first_message(label: str, message_stream):
    """Wrap a raw SDK message stream (query()'s return value) to log
    time-to-first-message - the closest external proxy available for
    "subprocess started, connected, and completed its first turn" without
    patching the SDK's own lazy connect() (see the vendored
    _internal/transport/subprocess_cli.py, which this project does not
    modify). This is item 9's "exact subprocess startup time"; forwards
    every message unchanged, same wrapping pattern as
    tracing.trace_specialist_run().
    """
    if not ENABLED:
        async for message in message_stream:
            yield message
        return

    start = time.monotonic()
    first = True
    async for message in message_stream:
        if first:
            elapsed = time.monotonic() - start
            print(
                f"[diag] {label} first_message_latency={round(elapsed, 3)}s "
                "(proxy for subprocess spawn + connect + first turn)",
                flush=True,
            )
            first = False
        yield message


def all_snapshots() -> list[dict[str, Any]]:
    """Every snapshot recorded so far in this process, as plain dicts -
    for a caller (app/diag_runner.py) that wants the raw data rather than
    the printed summary table.
    """
    return list(_snapshots)


def summarize() -> str:
    """A compact CSV-ish table of every snapshot taken so far in this
    process - print at the end of a diagnostic run (see app/diag_runner.py)
    for a copy-pasteable local-vs-Render comparison.
    """
    header = "label, t+s, self_rss_mb, self_cpu%, children, cgroup_mem_current/limit_mb, cgroup_cpu_cores, loadavg"
    lines = [header]
    for s in _snapshots:
        lines.append(
            f"{s['label']}, {s['elapsed_since_process_start_s']}, {s['self_rss_mb']}, "
            f"{s['self_cpu_percent']}, {s['child_process_count']}, "
            f"{s['cgroup_memory_current_mb']}/{s['cgroup_memory_limit_mb']}, "
            f"{s['cgroup_cpu_limit_cores']}, {s['loadavg_1_5_15']}"
        )
    return "\n".join(lines)
