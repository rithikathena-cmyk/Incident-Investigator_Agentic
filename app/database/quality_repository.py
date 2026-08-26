"""Pure database queries for quality data (Step 6).

This is the only module the quality tools query the database through. No
Claude Agent SDK code lives here, and conversely no SQL/ORM code lives in
agents/quality.py or tools/quality_tools.py.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.connection import get_session
from app.database.models import ProductionLine, QualityInspection


def normalize_line_id(raw: str) -> str:
    """Normalize inputs like 'Line 4', 'line4', '4', 'LINE-4' to 'LINE-4'."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits:
        return f"LINE-{int(digits)}"
    return raw.strip().upper()


def _parse_date(value: str) -> date_type | None:
    try:
        return date_type.fromisoformat(value.strip())
    except ValueError:
        return None


def _line_exists(session: Session, line_id: str) -> bool:
    return (
        session.execute(select(ProductionLine).where(ProductionLine.line_id == line_id)).scalar_one_or_none()
        is not None
    )


def _rows_for(session: Session, line_id: str, start: date_type, end: date_type) -> list[QualityInspection]:
    return list(
        session.execute(
            select(QualityInspection)
            .join(ProductionLine, QualityInspection.line_id == ProductionLine.id)
            .where(ProductionLine.line_id == line_id)
            .where(QualityInspection.inspection_date >= start)
            .where(QualityInspection.inspection_date <= end)
        ).scalars()
    )


def _defect_totals(rows: list[QualityInspection]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for r in rows:
        if r.defect_type:
            totals[r.defect_type] = totals.get(r.defect_type, 0) + r.rejected_quantity
    return totals


def _rejection_percentage(inspected: int, rejected: int) -> float:
    return round(rejected / inspected * 100, 2) if inspected else 0.0


def get_quality_metrics(line_id: str, date: str) -> dict[str, object] | None:
    """Inspected/rejected quantity, rejection percentage, and a defect
    summary for one line on one date. Everything here is computed from the
    raw quality_inspections rows for that line/date - nothing is stored.
    """
    normalized = normalize_line_id(line_id)
    parsed_date = _parse_date(date)
    if parsed_date is None:
        return None

    with get_session() as session:
        if not _line_exists(session, normalized):
            return None
        rows = _rows_for(session, normalized, parsed_date, parsed_date)

    inspected = sum(r.inspected_quantity for r in rows)
    rejected = sum(r.rejected_quantity for r in rows)
    defect_summary = sorted(
        ({"defect_type": k, "rejected_quantity": v} for k, v in _defect_totals(rows).items()),
        key=lambda d: -d["rejected_quantity"],
    )

    return {
        "line_id": normalized,
        "date": parsed_date.isoformat(),
        "inspected_quantity": inspected,
        "rejected_quantity": rejected,
        "rejection_percentage": _rejection_percentage(inspected, rejected),
        "defect_summary": defect_summary,
    }


def get_defect_distribution(line_id: str, date: str) -> dict[str, object] | None:
    """Defect types, their counts, and their share of that day's total
    rejects for one line on one date.
    """
    normalized = normalize_line_id(line_id)
    parsed_date = _parse_date(date)
    if parsed_date is None:
        return None

    with get_session() as session:
        if not _line_exists(session, normalized):
            return None
        rows = _rows_for(session, normalized, parsed_date, parsed_date)

    totals = _defect_totals(rows)
    total_rejected = sum(r.rejected_quantity for r in rows)

    defect_types = sorted(
        (
            {
                "defect_type": k,
                "rejected_quantity": v,
                "percentage_of_total_defects": round(v / total_rejected * 100, 2) if total_rejected else 0.0,
            }
            for k, v in totals.items()
        ),
        key=lambda d: -d["rejected_quantity"],
    )

    return {
        "line_id": normalized,
        "date": parsed_date.isoformat(),
        "total_rejected_quantity": total_rejected,
        "defect_types": defect_types,
    }


def compare_quality_history(line_id: str, date: str, lookback_days: int | str) -> dict[str, object] | None:
    """Current rejection rate vs. the historical average over the
    `lookback_days` days immediately before `date` (exclusive), plus a
    trend classification computed from the ratio - not hardcoded to any
    specific line/date.
    """
    normalized = normalize_line_id(line_id)
    parsed_date = _parse_date(date)
    if parsed_date is None:
        return None

    try:
        lookback = int(lookback_days)
    except (TypeError, ValueError):
        return None
    if lookback <= 0:
        return None

    with get_session() as session:
        if not _line_exists(session, normalized):
            return None
        current_rows = _rows_for(session, normalized, parsed_date, parsed_date)
        history_start = parsed_date - timedelta(days=lookback)
        history_end = parsed_date - timedelta(days=1)
        historical_rows = (
            _rows_for(session, normalized, history_start, history_end) if history_end >= history_start else []
        )

    current_inspected = sum(r.inspected_quantity for r in current_rows)
    current_rejected = sum(r.rejected_quantity for r in current_rows)
    current_rate = _rejection_percentage(current_inspected, current_rejected)

    hist_inspected = sum(r.inspected_quantity for r in historical_rows)
    hist_rejected = sum(r.rejected_quantity for r in historical_rows)
    historical_average_rate = _rejection_percentage(hist_inspected, hist_rejected)

    difference = round(current_rate - historical_average_rate, 2)

    if hist_inspected == 0:
        trend = "no_historical_baseline"
    elif current_rate >= historical_average_rate * 2:
        trend = "significantly_above_normal"
    elif current_rate > historical_average_rate * 1.2:
        trend = "above_normal"
    elif current_rate < historical_average_rate * 0.8:
        trend = "below_normal"
    else:
        trend = "normal"

    return {
        "line_id": normalized,
        "date": parsed_date.isoformat(),
        "lookback_days": lookback,
        "current_rejection_percentage": current_rate,
        "historical_average_rejection_percentage": historical_average_rate,
        "historical_days_with_data": len({r.inspection_date for r in historical_rows}),
        "difference_percentage_points": difference,
        "trend": trend,
    }
