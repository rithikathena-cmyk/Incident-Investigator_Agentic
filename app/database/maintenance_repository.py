"""Pure database queries for maintenance data (Step 5).

This is the only module the maintenance tools query the database through.
No Claude Agent SDK code lives here, and conversely no SQL/ORM code lives
in agents/maintenance.py or tools/maintenance_tools.py.
"""

from __future__ import annotations

from datetime import date as date_type

from sqlalchemy import select

from app.database.connection import get_session
from app.database.models import MaintenanceEvent, Machine, Production, ProductionLine

_SHIFT_ORDER = {"Day": 0, "Evening": 1, "Night": 2}


def normalize_machine_id(raw: str) -> str:
    """Normalize inputs like 'M-104', 'm104', 'M 104', '104' to 'M-104'."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits:
        return f"M-{digits}"
    return raw.strip().upper()


def normalize_line_id(raw: str) -> str:
    """Normalize inputs like 'Line 4', 'line4', '4', 'LINE-4' to 'LINE-4'.
    Same helper as production_repository.py/quality_repository.py - each
    repository module stays self-contained rather than importing a sibling.
    """
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits:
        return f"LINE-{int(digits)}"
    return raw.strip().upper()


def _parse_date(value: str) -> date_type | None:
    try:
        return date_type.fromisoformat(value.strip())
    except ValueError:
        return None


def _event_dict(event: MaintenanceEvent, *, include_date: bool) -> dict[str, object]:
    payload = {
        "event_type": event.event_type,
        "failure_type": event.failure_type,
        "description": event.description,
        "downtime_minutes": event.downtime_minutes,
        "technician": event.technician,
        "status": event.status,
    }
    if include_date:
        payload = {"maintenance_date": event.maintenance_date.isoformat(), **payload}
    return payload


def _shift_downtime_breakdown(production_rows: list[Production]) -> tuple[list[dict[str, object]], str | None]:
    shift_downtime = sorted(
        ({"shift": p.shift, "downtime_minutes": p.downtime_minutes} for p in production_rows),
        key=lambda s: _SHIFT_ORDER.get(s["shift"], 99),
    )
    affected_shift = (
        max(shift_downtime, key=lambda s: s["downtime_minutes"])["shift"] if shift_downtime else None
    )
    return shift_downtime, affected_shift


def get_machine_downtime(machine_id: str, date: str) -> dict[str, object] | None:
    """Maintenance downtime + events for one machine on one date, with the
    most-affected shift noted if production records exist for that day.

    Returns None only if the machine itself doesn't exist. A machine that
    exists but had no maintenance that day still returns a real result with
    downtime_minutes=0 and an empty events list - "nothing happened" is a
    finding too, not an error.
    """
    normalized = normalize_machine_id(machine_id)
    parsed_date = _parse_date(date)
    if parsed_date is None:
        return None

    with get_session() as session:
        machine = session.execute(
            select(Machine).where(Machine.machine_id == normalized)
        ).scalar_one_or_none()
        if machine is None:
            return None

        events = session.execute(
            select(MaintenanceEvent)
            .where(MaintenanceEvent.machine_id == machine.id)
            .where(MaintenanceEvent.maintenance_date == parsed_date)
            .order_by(MaintenanceEvent.id)
        ).scalars().all()

        production_rows = session.execute(
            select(Production)
            .where(Production.machine_id == machine.id)
            .where(Production.production_date == parsed_date)
        ).scalars().all()

    shift_downtime, affected_shift = _shift_downtime_breakdown(production_rows)

    return {
        "machine_id": normalized,
        "date": parsed_date.isoformat(),
        "downtime_minutes": sum(e.downtime_minutes for e in events),
        "events": [_event_dict(e, include_date=False) for e in events],
        "affected_shift": affected_shift,
        "shift_downtime_breakdown": shift_downtime,
    }


def get_line_downtime(line_id: str, date: str) -> dict[str, object] | None:
    """Maintenance downtime + events for every machine on one line on one
    date, in a single query - the real Machine.line_id relationship answers
    "which machine on this line went down" directly, instead of the caller
    checking machines one at a time across the whole fleet. Machines are
    returned sorted worst-downtime-first. Returns None only if the line
    itself doesn't exist; a line with no maintenance that day still returns
    a real result with every machine at downtime_minutes=0.
    """
    normalized_line = normalize_line_id(line_id)
    parsed_date = _parse_date(date)
    if parsed_date is None:
        return None

    with get_session() as session:
        line = session.execute(
            select(ProductionLine).where(ProductionLine.line_id == normalized_line)
        ).scalar_one_or_none()
        if line is None:
            return None

        machines = session.execute(
            select(Machine).where(Machine.line_id == line.id).order_by(Machine.machine_id)
        ).scalars().all()
        machine_pks = [m.id for m in machines]

        events = session.execute(
            select(MaintenanceEvent)
            .where(MaintenanceEvent.machine_id.in_(machine_pks))
            .where(MaintenanceEvent.maintenance_date == parsed_date)
            .order_by(MaintenanceEvent.id)
        ).scalars().all()

        production_rows = session.execute(
            select(Production)
            .where(Production.machine_id.in_(machine_pks))
            .where(Production.production_date == parsed_date)
        ).scalars().all()

    events_by_machine: dict[int, list[MaintenanceEvent]] = {}
    for e in events:
        events_by_machine.setdefault(e.machine_id, []).append(e)

    production_by_machine: dict[int, list[Production]] = {}
    for p in production_rows:
        production_by_machine.setdefault(p.machine_id, []).append(p)

    per_machine = []
    for m in machines:
        m_events = events_by_machine.get(m.id, [])
        shift_downtime, affected_shift = _shift_downtime_breakdown(production_by_machine.get(m.id, []))
        per_machine.append(
            {
                "machine_id": m.machine_id,
                "downtime_minutes": sum(e.downtime_minutes for e in m_events),
                "events": [_event_dict(e, include_date=False) for e in m_events],
                "affected_shift": affected_shift,
                "shift_downtime_breakdown": shift_downtime,
            }
        )
    per_machine.sort(key=lambda m: -m["downtime_minutes"])

    return {
        "line_id": normalized_line,
        "date": parsed_date.isoformat(),
        "machines": per_machine,
    }


def get_maintenance_events(machine_id: str, start_date: str, end_date: str) -> dict[str, object] | None:
    """Maintenance events for one machine over a date range."""
    normalized = normalize_machine_id(machine_id)
    parsed_start = _parse_date(start_date)
    parsed_end = _parse_date(end_date)
    if parsed_start is None or parsed_end is None:
        return None

    with get_session() as session:
        machine = session.execute(
            select(Machine).where(Machine.machine_id == normalized)
        ).scalar_one_or_none()
        if machine is None:
            return None

        events = session.execute(
            select(MaintenanceEvent)
            .where(MaintenanceEvent.machine_id == machine.id)
            .where(MaintenanceEvent.maintenance_date >= parsed_start)
            .where(MaintenanceEvent.maintenance_date <= parsed_end)
            .order_by(MaintenanceEvent.maintenance_date)
        ).scalars().all()

    event_list = [_event_dict(e, include_date=True) for e in events]

    return {
        "machine_id": normalized,
        "start_date": parsed_start.isoformat(),
        "end_date": parsed_end.isoformat(),
        "total_downtime_minutes": sum(e["downtime_minutes"] for e in event_list),
        "events": event_list,
    }


def get_machine_history(machine_id: str) -> dict[str, object] | None:
    """A machine's full maintenance history, plus failure-frequency stats
    computed from the raw events (nothing here is stored/hardcoded).
    """
    normalized = normalize_machine_id(machine_id)

    with get_session() as session:
        machine = session.execute(
            select(Machine).where(Machine.machine_id == normalized)
        ).scalar_one_or_none()
        if machine is None:
            return None

        events = session.execute(
            select(MaintenanceEvent)
            .where(MaintenanceEvent.machine_id == machine.id)
            .order_by(MaintenanceEvent.maintenance_date)
        ).scalars().all()

    event_list = [_event_dict(e, include_date=True) for e in events]

    failure_type_counts: dict[str, int] = {}
    for e in event_list:
        failure_type = e["failure_type"]
        if failure_type:
            failure_type_counts[failure_type] = failure_type_counts.get(failure_type, 0) + 1

    recurring_failure_types = sorted(
        (ft for ft, count in failure_type_counts.items() if count > 1),
        key=lambda ft: -failure_type_counts[ft],
    )

    return {
        "machine_id": normalized,
        "total_events": len(event_list),
        "failure_event_count": sum(1 for e in event_list if e["failure_type"]),
        "total_downtime_minutes": sum(e["downtime_minutes"] for e in event_list),
        "failure_type_counts": failure_type_counts,
        "recurring_failure_types": recurring_failure_types,
        "events": event_list,
    }
