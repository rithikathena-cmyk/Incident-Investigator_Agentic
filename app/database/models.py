"""SQLAlchemy ORM models for the manufacturing production schema (Step 4).

No root_cause column anywhere on purpose - any anomaly has to be found by
reading planned/actual/downtime numbers, not by reading a label.
"""

from __future__ import annotations

from datetime import date as date_

from sqlalchemy import Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ProductionLine(Base):
    __tablename__ = "production_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    line_id: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    line_name: Mapped[str] = mapped_column(String(100), nullable=False)
    department: Mapped[str] = mapped_column(String(100), nullable=False)

    machines: Mapped[list["Machine"]] = relationship(back_populates="line")
    production_records: Mapped[list["Production"]] = relationship(back_populates="line")
    quality_inspections: Mapped[list["QualityInspection"]] = relationship(back_populates="line")


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    machine_name: Mapped[str] = mapped_column(String(100), nullable=False)
    line_id: Mapped[int] = mapped_column(ForeignKey("production_lines.id"), nullable=False)
    machine_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")

    line: Mapped[ProductionLine] = relationship(back_populates="machines")
    production_records: Mapped[list["Production"]] = relationship(back_populates="machine")
    maintenance_events: Mapped[list["MaintenanceEvent"]] = relationship(back_populates="machine")
    quality_inspections: Mapped[list["QualityInspection"]] = relationship(back_populates="machine")


class Production(Base):
    __tablename__ = "production"
    __table_args__ = (
        UniqueConstraint(
            "line_id", "machine_id", "production_date", "shift", name="uq_production_slot"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("production_lines.id"), nullable=False)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), nullable=False)
    production_date: Mapped[date_] = mapped_column(Date, nullable=False, index=True)
    shift: Mapped[str] = mapped_column(String(20), nullable=False)
    planned_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    downtime_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    line: Mapped[ProductionLine] = relationship(back_populates="production_records")
    machine: Mapped[Machine] = relationship(back_populates="production_records")


class MaintenanceEvent(Base):
    """A maintenance event on one machine (Step 5): a repair, preventive
    service, or inspection. No root_cause column - failure_type is a
    category (e.g. "Motor Failure"), description is free text; anything
    beyond that has to come from the agent reading the records itself.
    """

    __tablename__ = "maintenance_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), nullable=False)
    maintenance_date: Mapped[date_] = mapped_column(Date, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    failure_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    downtime_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    technician: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    machine: Mapped[Machine] = relationship(back_populates="maintenance_events")


class QualityInspection(Base):
    """A quality inspection result on one machine/shift/date (Step 6). No
    root_cause column - defect_type is a category (e.g. "Assembly Defect"),
    defect_description is free text; a machine/shift/date being an outlier
    has to come from comparing rejection rates, not from a flag column.
    """

    __tablename__ = "quality_inspections"
    __table_args__ = (
        UniqueConstraint(
            "line_id", "machine_id", "inspection_date", "shift", name="uq_quality_slot"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("production_lines.id"), nullable=False)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), nullable=False)
    inspection_date: Mapped[date_] = mapped_column(Date, nullable=False, index=True)
    shift: Mapped[str] = mapped_column(String(20), nullable=False)
    inspected_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    defect_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    defect_description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    line: Mapped[ProductionLine] = relationship(back_populates="quality_inspections")
    machine: Mapped[Machine] = relationship(back_populates="quality_inspections")


class User(Base):
    """A login account (auth, not RBAC - see guardrails.ROLE_DOMAIN_TABLE for
    the separate, pre-existing investigation-domain role picker, which login
    does not replace). role is either "admin" or "user"; admin's only extra
    privilege today is reading the guardrail/capability audit log.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
