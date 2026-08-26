"""Deterministic capability/permission layer (Step 9).

The single, code-only authority for which agent may use which tool, and
which agents the Supervisor may delegate to. No LLM is ever consulted here
- authorize() and authorize_delegation() are pure dict lookups. Every
decision is recorded to an in-memory audit log.

Only lists capabilities for tools that actually exist in this codebase.
get_shift_production and compare_production_history (mentioned in some
capability specs during design discussion) were never implemented as real
tools in any prior step - only get_production_metrics exists for the
Production Agent - so they are intentionally absent here rather than
fabricated.
"""

from __future__ import annotations

import contextvars
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

Decision = Literal["ALLOW", "DENY"]

# The single source of truth for which tools each agent may use. Each
# specialist gets exactly its own domain's tools - nothing is shared
# across specialists (e.g. Maintenance does not get Knowledge's tool;
# see the Step 9 design discussion for why).
CAPABILITY_TABLE: dict[str, frozenset[str]] = {
    "production": frozenset({"get_production_metrics", "get_production_metrics_batch"}),
    "maintenance": frozenset(
        {
            "get_machine_downtime",
            "get_line_downtime",
            "get_line_downtime_batch",
            "get_maintenance_events",
            "get_machine_history",
        }
    ),
    "quality": frozenset(
        {"get_quality_metrics", "get_quality_metrics_batch", "get_defect_distribution", "compare_quality_history"}
    ),
    "knowledge": frozenset({"search_manufacturing_knowledge"}),
    # The Supervisor has NO direct data-tool capability at all - it can
    # only delegate, checked separately via DELEGATION_TABLE/authorize_delegation.
    "supervisor": frozenset(),
}

# Delegation gate (Step 9 section 4): which agents the Supervisor may
# delegate an investigation to.
DELEGATION_TABLE: dict[str, frozenset[str]] = {
    "supervisor": frozenset({"production", "maintenance", "quality", "knowledge"}),
}

# Categorically forbidden regardless of agent - not looked up per-agent,
# just documented here for clarity. No code path in this project ever
# grants any of these to any agent (see each agent's disallowed_tools).
CATEGORICALLY_FORBIDDEN = frozenset(
    {
        "database_write",
        "database_delete",
        "qdrant_write",
        "qdrant_delete",
        "shell_execution",
        "filesystem_modification",
        "rbac_modification",
        "permission_bypass",
    }
)

current_investigation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_investigation_id", default=None
)


def new_investigation_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class CapabilityDecision:
    agent: str
    capability: str
    decision: Decision
    reason: str
    timestamp: str
    investigation_id: str | None

    @property
    def allowed(self) -> bool:
        return self.decision == "ALLOW"

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "agent": self.agent,
            "capability": self.capability,
            "decision": self.decision,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "investigation_id": self.investigation_id,
        }


_AUDIT_LOG: list[CapabilityDecision] = []


def _record(decision: CapabilityDecision) -> CapabilityDecision:
    _AUDIT_LOG.append(decision)
    return decision


def get_audit_log() -> list[CapabilityDecision]:
    """A copy of every decision made so far, oldest first."""
    return list(_AUDIT_LOG)


def clear_audit_log() -> None:
    """Test isolation helper - not used in normal operation."""
    _AUDIT_LOG.clear()


def authorize(agent: str, capability: str) -> CapabilityDecision:
    """The tool execution gate's decision function.

    ALLOW iff `capability` is in `agent`'s entry in CAPABILITY_TABLE.
    Pure, deterministic, synchronous - never calls Claude.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    investigation_id = current_investigation_id.get()
    allowed_capabilities = CAPABILITY_TABLE.get(agent)

    if allowed_capabilities is None:
        decision = CapabilityDecision(
            agent=agent,
            capability=capability,
            decision="DENY",
            reason=f"Unknown agent '{agent}'.",
            timestamp=timestamp,
            investigation_id=investigation_id,
        )
    elif capability in allowed_capabilities:
        decision = CapabilityDecision(
            agent=agent,
            capability=capability,
            decision="ALLOW",
            reason=f"'{capability}' is in {agent}'s capability allowlist.",
            timestamp=timestamp,
            investigation_id=investigation_id,
        )
    else:
        decision = CapabilityDecision(
            agent=agent,
            capability=capability,
            decision="DENY",
            reason=f"Capability not permitted for {agent} agent.",
            timestamp=timestamp,
            investigation_id=investigation_id,
        )
    return _record(decision)


def authorize_delegation(from_agent: str, to_agent: str) -> CapabilityDecision:
    """The delegation gate's decision function (Step 9 section 4).

    ALLOW iff `to_agent` is in `from_agent`'s entry in DELEGATION_TABLE.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    investigation_id = current_investigation_id.get()
    allowed_targets = DELEGATION_TABLE.get(from_agent, frozenset())
    capability = f"delegate_to_{to_agent}_agent"

    if to_agent in allowed_targets:
        decision = CapabilityDecision(
            agent=from_agent,
            capability=capability,
            decision="ALLOW",
            reason=f"{from_agent} may delegate to {to_agent}.",
            timestamp=timestamp,
            investigation_id=investigation_id,
        )
    else:
        decision = CapabilityDecision(
            agent=from_agent,
            capability=capability,
            decision="DENY",
            reason=f"{from_agent} is not permitted to delegate to '{to_agent}'.",
            timestamp=timestamp,
            investigation_id=investigation_id,
        )
    return _record(decision)
