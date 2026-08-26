"""Deterministic guardrail + RBAC layer (Phase 12).

USER -> INPUT GUARDRAILS -> RBAC -> Supervisor -> ... -> OUTPUT GUARDRAILS -> USER

Every check in this module is plain Python (regex/keyword/structural
heuristics) - no Claude Agent SDK import exists anywhere in this file, and
no function here ever calls Claude. "Is this a prompt injection / is this
in scope / is this PII / is this evidence sufficient" are all answered by
code, mirroring capabilities.py's "authorize() never asks Claude" design.

These are real, working checks, not stubs - but they are pattern/heuristic
based, not semantic understanding, because semantic understanding would
require an LLM call, which the brief explicitly rules out for security
decisions. That tradeoff is deliberate: fast, cheap, deterministic, and
adversary-resistant to being talked out of a decision - at the cost of not
catching every cleverly-worded case a human (or an LLM) would.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

Decision = Literal["ALLOW", "DENY", "WARN"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class GuardrailDecision:
    stage: str  # "input" | "rbac" | "output"
    check: str
    decision: Decision
    reason: str
    timestamp: str
    investigation_id: str | None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision != "DENY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "check": self.check,
            "decision": self.decision,
            "allowed": self.allowed,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "investigation_id": self.investigation_id,
            "details": self.details,
        }


_AUDIT_LOG: list[GuardrailDecision] = []


def _record(decision: GuardrailDecision) -> GuardrailDecision:
    _AUDIT_LOG.append(decision)
    return decision


def get_audit_log() -> list[GuardrailDecision]:
    return list(_AUDIT_LOG)


def clear_audit_log() -> None:
    """Test isolation helper - not used in normal operation."""
    _AUDIT_LOG.clear()


# ============================================================================
# Input guardrails
# ============================================================================

_INJECTION_PATTERNS = [
    r"\bignore\s+(?:your|previous|all|the\s+above)\s+instructions?\b",
    r"\bignore\s+(?:the\s+)?above\b",
    r"\bdisregard\s+(?:your|previous|all)\s+instructions?\b",
    r"\bforget\s+(?:your|previous|all)\s+instructions?\b",
    r"\byou\s+are\s+now\b",
    r"\bact\s+as\s+(?:a|an|if)\b",
    r"\bpretend\s+(?:you\s+are|to\s+be)\b",
    r"\breveal\s+(?:your\s+)?(?:system\s+)?prompt\b",
    r"\bshow\s+(?:me\s+)?(?:your\s+)?(?:system\s+)?prompt\b",
    r"\bexpose\s+the\s+database\b",
    r"\bbypass\s+(?:your|the)\s+(?:permission|security|capability|capabilities)\b",
    r"\boverride\s+(?:your\s+)?rules\b",
    r"\bignore\s+(?:your\s+)?(?:agent\s+)?permissions\b",
    r"\bjailbreak\b",
    r"\bdeveloper\s+mode\b",
    r"\bsystem\s*:\s*you\s+(?:are|must)\b",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


def check_prompt_injection(question: str, *, investigation_id: str | None = None) -> GuardrailDecision:
    match = _INJECTION_RE.search(question)
    if match:
        return _record(
            GuardrailDecision(
                stage="input",
                check="prompt_injection",
                decision="DENY",
                reason=f"Question matches a known prompt-injection pattern: {match.group(0)!r}.",
                timestamp=_now(),
                investigation_id=investigation_id,
                details={"matched_text": match.group(0)},
            )
        )
    return _record(
        GuardrailDecision(
            stage="input",
            check="prompt_injection",
            decision="ALLOW",
            reason="No known prompt-injection pattern matched.",
            timestamp=_now(),
            investigation_id=investigation_id,
        )
    )


_DOMAIN_KEYWORDS: dict[str, frozenset[str]] = {
    "production": frozenset(
        {"production", "output", "quantity", "throughput", "yield", "planned", "actual", "loss"}
    ),
    "maintenance": frozenset(
        {
            "maintenance", "downtime", "breakdown", "failure", "failed", "repair", "motor",
            "bearing", "winding", "technician", "machine history", "corrective", "preventive",
        }
    ),
    "quality": frozenset(
        {"quality", "defect", "reject", "rejection", "inspection", "scrap", "tolerance"}
    ),
    "knowledge": frozenset(
        {"sop", "procedure", "documentation", "manual", "guideline", "recommend", "policy"}
    ),
}
_ALL_DOMAIN_KEYWORDS: frozenset[str] = frozenset().union(*_DOMAIN_KEYWORDS.values())

_GENERIC_INVESTIGATION_KEYWORDS = frozenset(
    {
        "line", "machine", "plant", "factory", "shift", "assembly", "conveyor",
        "root cause", "incident", "investigate", "investigation", "why did",
    }
)

_OFF_TOPIC_KEYWORDS = frozenset(
    {
        "weather", "forecast", "temperature outside", "rain", "snow forecast",
        "sports score", "football score", "basketball score", "joke", "tell me a joke",
        "recipe", "cook", "movie recommendation", "celebrity", "horoscope",
        "stock price", "cryptocurrency price", "song lyrics", "poem",
    }
)


def classify_domains(question: str) -> set[str]:
    """Deterministic keyword classification of which specialist domain(s)
    a question plausibly touches. Used both for the scope check and for RBAC.
    """
    lowered = question.lower()
    return {domain for domain, keywords in _DOMAIN_KEYWORDS.items() if any(k in lowered for k in keywords)}


def check_scope(question: str, *, investigation_id: str | None = None) -> GuardrailDecision:
    lowered = question.lower()
    domains = classify_domains(question)
    has_domain_signal = bool(domains) or any(k in lowered for k in _GENERIC_INVESTIGATION_KEYWORDS)
    has_off_topic_signal = any(k in lowered for k in _OFF_TOPIC_KEYWORDS)

    if has_off_topic_signal and not has_domain_signal:
        return _record(
            GuardrailDecision(
                stage="input",
                check="scope",
                decision="DENY",
                reason="Question does not appear related to manufacturing production/maintenance/quality/knowledge.",
                timestamp=_now(),
                investigation_id=investigation_id,
            )
        )
    return _record(
        GuardrailDecision(
            stage="input",
            check="scope",
            decision="ALLOW",
            reason="Question is in scope or ambiguous enough to let the Supervisor judge.",
            timestamp=_now(),
            investigation_id=investigation_id,
            details={"classified_domains": sorted(domains)},
        )
    )


_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CREDIT_CARD_RE = re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b")
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")

_PII_PATTERNS = {
    "email": _EMAIL_RE,
    "ssn": _SSN_RE,
    "credit_card": _CREDIT_CARD_RE,
    "phone": _PHONE_RE,
}


def find_pii(text: str) -> dict[str, list[str]]:
    """Regex-based PII scan. Deliberately scoped to structured, machine-
    checkable patterns (not names) - detecting a name is a semantic task
    that would require an LLM, which security decisions here must not use.
    """
    found: dict[str, list[str]] = {}
    for kind, pattern in _PII_PATTERNS.items():
        matches = pattern.findall(text)
        if matches:
            found[kind] = matches
    return found


def redact_pii(text: str) -> str:
    redacted = text
    for pattern in _PII_PATTERNS.values():
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def check_input_pii(question: str, *, investigation_id: str | None = None) -> GuardrailDecision:
    found = find_pii(question)
    if found:
        return _record(
            GuardrailDecision(
                stage="input",
                check="pii",
                decision="DENY",
                reason=f"Question appears to contain PII: {', '.join(sorted(found))}.",
                timestamp=_now(),
                investigation_id=investigation_id,
                details={"pii_types": sorted(found)},
            )
        )
    return _record(
        GuardrailDecision(
            stage="input",
            check="pii",
            decision="ALLOW",
            reason="No PII pattern detected in the question.",
            timestamp=_now(),
            investigation_id=investigation_id,
        )
    )


_DESTRUCTIVE_VERBS = frozenset(
    {"delete", "drop", "truncate", "remove", "wipe", "destroy", "purge", "erase", "clear out"}
)
_DATA_TARGET_NOUNS = frozenset(
    {
        "record", "records", "table", "tables", "database", "data", "row", "rows",
        "entry", "entries", "collection", "index",
    }
)
_SECURITY_BYPASS_PHRASES = frozenset(
    {"disable security", "disable permissions", "turn off security", "bypass permissions", "grant me admin"}
)


def check_harmful_intent(question: str, *, investigation_id: str | None = None) -> GuardrailDecision:
    lowered = question.lower()
    has_destructive_verb = any(re.search(rf"\b{re.escape(v)}\b", lowered) for v in _DESTRUCTIVE_VERBS)
    has_data_target = any(re.search(rf"\b{re.escape(n)}\b", lowered) for n in _DATA_TARGET_NOUNS)
    has_bypass_phrase = any(p in lowered for p in _SECURITY_BYPASS_PHRASES)

    if (has_destructive_verb and has_data_target) or has_bypass_phrase:
        return _record(
            GuardrailDecision(
                stage="input",
                check="harmful_intent",
                decision="DENY",
                reason="Question requests a destructive data operation or a security bypass, which this "
                "read-only investigation system never performs.",
                timestamp=_now(),
                investigation_id=investigation_id,
            )
        )
    return _record(
        GuardrailDecision(
            stage="input",
            check="harmful_intent",
            decision="ALLOW",
            reason="No destructive-operation or security-bypass pattern matched.",
            timestamp=_now(),
            investigation_id=investigation_id,
        )
    )


def check_input(question: str, *, investigation_id: str | None = None) -> list[GuardrailDecision]:
    """Run all four input guardrail checks. The caller should treat the
    request as blocked if any decision in the returned list is DENY.
    """
    return [
        check_prompt_injection(question, investigation_id=investigation_id),
        check_scope(question, investigation_id=investigation_id),
        check_input_pii(question, investigation_id=investigation_id),
        check_harmful_intent(question, investigation_id=investigation_id),
    ]


# ============================================================================
# RBAC
# ============================================================================

# Which manufacturing domains each role may investigate. No authentication
# is implemented (verifying *who* the caller is) - this is authorization
# only, exactly like capabilities.py's CAPABILITY_TABLE, just at the
# user-role level instead of the agent level. Callers that don't specify a
# role get DEFAULT_ROLE, which has full access - the same access the system
# had before RBAC existed, so nothing already built changes behavior by
# default.
DEFAULT_ROLE = "plant_engineer"

ROLE_DOMAIN_TABLE: dict[str, frozenset[str]] = {
    "plant_engineer": frozenset({"production", "maintenance", "quality", "knowledge"}),
    "quality_auditor": frozenset({"quality", "knowledge"}),
    "maintenance_technician": frozenset({"maintenance", "knowledge"}),
    "guest": frozenset({"knowledge"}),
}


def authorize_domains(role: str, domains: set[str], *, investigation_id: str | None = None) -> GuardrailDecision:
    """RBAC gate: may `role` investigate all of `domains`? An empty
    `domains` set (a general/ambiguous question) is always allowed - RBAC
    only blocks a request that clearly implicates a domain the role can't
    access.
    """
    allowed_domains = ROLE_DOMAIN_TABLE.get(role, frozenset())
    denied_domains = sorted(domains - allowed_domains)

    if role not in ROLE_DOMAIN_TABLE:
        return _record(
            GuardrailDecision(
                stage="rbac",
                check="rbac",
                decision="DENY",
                reason=f"Unknown role '{role}'.",
                timestamp=_now(),
                investigation_id=investigation_id,
            )
        )

    if denied_domains:
        return _record(
            GuardrailDecision(
                stage="rbac",
                check="rbac",
                decision="DENY",
                reason=f"Role '{role}' is not authorized for domain(s): {', '.join(denied_domains)}.",
                timestamp=_now(),
                investigation_id=investigation_id,
                details={"role": role, "requested_domains": sorted(domains), "denied_domains": denied_domains},
            )
        )
    return _record(
        GuardrailDecision(
            stage="rbac",
            check="rbac",
            decision="ALLOW",
            reason=f"Role '{role}' is authorized for domain(s): {', '.join(sorted(domains)) or '(none specifically requested)'}.",
            timestamp=_now(),
            investigation_id=investigation_id,
            details={"role": role, "requested_domains": sorted(domains)},
        )
    )


# ============================================================================
# Output guardrails
# ============================================================================

_MIN_EVIDENCE_FOR_HIGH_CONFIDENCE = 3
_HIGH_CONFIDENCE_THRESHOLD = 0.9


def _report_text_fields(report: dict[str, Any]) -> list[str]:
    texts = [str(report.get("root_cause", "")), *[str(e) for e in report.get("evidence", [])]]
    for factor in report.get("contributing_factors", []) or []:
        texts.append(str(factor))
    for finding in report.get("findings", []) or []:
        texts.append(str(finding.get("finding", "")))
        texts.extend(str(e) for e in finding.get("evidence", []) or [])
    return texts


def check_output_pii(report: dict[str, Any], *, investigation_id: str | None = None) -> tuple[GuardrailDecision, dict[str, Any]]:
    """Scan every text field of the final report for PII. Returns the
    decision plus a redacted copy of the report (identical to the input if
    nothing was found).
    """
    all_found: dict[str, list[str]] = {}
    for text in _report_text_fields(report):
        for kind, matches in find_pii(text).items():
            all_found.setdefault(kind, []).extend(matches)

    if not all_found:
        decision = _record(
            GuardrailDecision(
                stage="output",
                check="pii",
                decision="ALLOW",
                reason="No PII pattern detected in the report.",
                timestamp=_now(),
                investigation_id=investigation_id,
            )
        )
        return decision, report

    redacted_report = _redact_report(report)
    decision = _record(
        GuardrailDecision(
            stage="output",
            check="pii",
            decision="WARN",
            reason=f"PII detected and redacted from report: {', '.join(sorted(all_found))}.",
            timestamp=_now(),
            investigation_id=investigation_id,
            details={"pii_types": sorted(all_found)},
        )
    )
    return decision, redacted_report


def _redact_report(report: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = dict(report)
    redacted["root_cause"] = redact_pii(str(report.get("root_cause", "")))
    redacted["evidence"] = [redact_pii(str(e)) for e in report.get("evidence", []) or []]
    redacted["contributing_factors"] = [redact_pii(str(f)) for f in report.get("contributing_factors", []) or []]
    redacted["findings"] = [
        {
            **finding,
            "finding": redact_pii(str(finding.get("finding", ""))),
            "evidence": [redact_pii(str(e)) for e in finding.get("evidence", []) or []],
        }
        for finding in report.get("findings", []) or []
    ]
    return redacted


def check_output_evidence_requirement(report: dict[str, Any], *, investigation_id: str | None = None) -> GuardrailDecision:
    """A root_cause must be backed by at least one piece of top-level
    evidence, and every individual finding must cite its own evidence.
    Purely structural (list presence/length), not semantic entailment -
    verifying a claim is actually *supported* by its evidence would require
    an LLM, which this module must not use.
    """
    root_cause = str(report.get("root_cause", "")).strip()
    evidence = report.get("evidence", []) or []
    findings = report.get("findings", []) or []

    problems: list[str] = []
    if root_cause and not evidence:
        problems.append("root_cause is stated but the top-level evidence list is empty")
    for finding in findings:
        if str(finding.get("finding", "")).strip() and not (finding.get("evidence") or []):
            problems.append(f"{finding.get('agent', 'unknown')} finding has no evidence")

    if problems:
        return _record(
            GuardrailDecision(
                stage="output",
                check="evidence_requirement",
                decision="WARN",
                reason="; ".join(problems),
                timestamp=_now(),
                investigation_id=investigation_id,
            )
        )
    return _record(
        GuardrailDecision(
            stage="output",
            check="evidence_requirement",
            decision="ALLOW",
            reason="root_cause and every finding are backed by at least one evidence item.",
            timestamp=_now(),
            investigation_id=investigation_id,
        )
    )


def check_output_confidence_calibration(report: dict[str, Any], *, investigation_id: str | None = None) -> GuardrailDecision:
    """Flags an unsupported claim in the narrow, checkable sense of "high
    confidence, thin evidence" - a structural proxy for "unsupported
    claims", since verifying true semantic support needs an LLM.
    """
    confidence = report.get("confidence")
    evidence_count = len(report.get("evidence", []) or [])

    if isinstance(confidence, (int, float)) and confidence >= _HIGH_CONFIDENCE_THRESHOLD and evidence_count < _MIN_EVIDENCE_FOR_HIGH_CONFIDENCE:
        return _record(
            GuardrailDecision(
                stage="output",
                check="confidence_calibration",
                decision="WARN",
                reason=f"Confidence {confidence} is >= {_HIGH_CONFIDENCE_THRESHOLD} but only {evidence_count} "
                f"top-level evidence item(s) were given (expected >= {_MIN_EVIDENCE_FOR_HIGH_CONFIDENCE}).",
                timestamp=_now(),
                investigation_id=investigation_id,
            )
        )
    return _record(
        GuardrailDecision(
            stage="output",
            check="confidence_calibration",
            decision="ALLOW",
            reason="Confidence level is consistent with the amount of evidence provided.",
            timestamp=_now(),
            investigation_id=investigation_id,
        )
    )


def check_output(report: dict[str, Any], *, investigation_id: str | None = None) -> tuple[list[GuardrailDecision], dict[str, Any]]:
    """Run all output guardrail checks. Returns the decisions plus the
    (possibly PII-redacted) report to actually show the user.
    """
    pii_decision, redacted_report = check_output_pii(report, investigation_id=investigation_id)
    decisions = [
        pii_decision,
        check_output_evidence_requirement(redacted_report, investigation_id=investigation_id),
        check_output_confidence_calibration(redacted_report, investigation_id=investigation_id),
    ]
    return decisions, redacted_report
