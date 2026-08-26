"""Pure database queries for production metrics (Step 4).

This is the ONLY module that queries the database. No Claude Agent SDK code
lives here, and conversely no SQL/ORM code lives in agents/production.py or
tools/production_tools.py - the tool just calls get_production_metrics().
"""

from __future__ import annotations

from datetime import date as date_type

from sqlalchemy import select

from app.database.connection import get_session
from app.database.models import Machine, Production, ProductionLine


def normalize_line_id(raw: str) -> str:
    """Normalize inputs like 'Line 4', 'line4', '4', 'LINE-4' to 'LINE-4'."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits:
        return f"LINE-{int(digits)}"
    return raw.strip().upper()


def available_line_ids() -> list[str]:
    with get_session() as session:
        rows = session.execute(
            select(ProductionLine.line_id).order_by(ProductionLine.line_id)
        ).scalars()
        return list(rows)


def available_dates(line_id: str) -> list[str]:
    normalized = normalize_line_id(line_id)
    with get_session() as session:
        rows = session.execute(
            select(Production.production_date)
            .join(ProductionLine, Production.line_id == ProductionLine.id)
            .where(ProductionLine.line_id == normalized)
            .distinct()
            .order_by(Production.production_date)
        ).scalars()
        return [d.isoformat() for d in rows]


def _loss_percentage(planned: int, actual: int) -> float:
    if planned <= 0:
        return 0.0
    return round((planned - actual) / planned * 100, 2)


def get_production_metrics(line_id: str, date: str) -> dict[str, object] | None:
    """Query PostgreSQL for one line/date and compute metrics from the raw
    per-machine, per-shift records. Returns None if there's no data.

    Every number in the result - including which shift/machine looks
    anomalous - is derived here from the `production` table; nothing is a
    stored or hardcoded answer, and there is no root_cause column to read.
    """
    normalized = normalize_line_id(line_id)
    try:
        parsed_date = date_type.fromisoformat(date.strip())
    except ValueError:
        return None

    with get_session() as session:
        rows = session.execute(
            select(Production, Machine)
            .join(ProductionLine, Production.line_id == ProductionLine.id)
            .join(Machine, Production.machine_id == Machine.id)
            .where(ProductionLine.line_id == normalized)
            .where(Production.production_date == parsed_date)
        ).all()

    if not rows:
        return None

    planned_quantity = sum(p.planned_quantity for p, _m in rows)
    actual_quantity = sum(p.actual_quantity for p, _m in rows)
    downtime_minutes = sum(p.downtime_minutes for p, _m in rows)

    by_shift: dict[str, list[tuple[Production, Machine]]] = {}
    for production, machine in rows:
        by_shift.setdefault(production.shift, []).append((production, machine))

    shift_order = {"Day": 0, "Evening": 1, "Night": 2}
    shift_breakdown = []
    for shift_name, entries in sorted(by_shift.items(), key=lambda kv: shift_order.get(kv[0], 99)):
        shift_planned = sum(p.planned_quantity for p, _m in entries)
        shift_actual = sum(p.actual_quantity for p, _m in entries)
        shift_downtime = sum(p.downtime_minutes for p, _m in entries)
        machines = [
            {
                "machine_id": m.machine_id,
                "machine_name": m.machine_name,
                "planned_quantity": p.planned_quantity,
                "actual_quantity": p.actual_quantity,
                "downtime_minutes": p.downtime_minutes,
                "production_loss_percentage": _loss_percentage(p.planned_quantity, p.actual_quantity),
            }
            for p, m in entries
        ]
        shift_breakdown.append(
            {
                "shift": shift_name,
                "planned_quantity": shift_planned,
                "actual_quantity": shift_actual,
                "downtime_minutes": shift_downtime,
                "production_loss_percentage": _loss_percentage(shift_planned, shift_actual),
                "machines": machines,
            }
        )

    return {
        "line_id": normalized,
        "date": parsed_date.isoformat(),
        "planned_quantity": planned_quantity,
        "actual_quantity": actual_quantity,
        "production_loss_percentage": _loss_percentage(planned_quantity, actual_quantity),
        "downtime_minutes": downtime_minutes,
        "shift_breakdown": shift_breakdown,
    }
