"""Seed script (Steps 4-6): populate PostgreSQL with synthetic data.

Run with:  python -m app.database.seed

Deterministic (fixed RNG seeds) so re-running gives the same dataset. Resets
the schema first, so it's safe to run repeatedly during development.

Generates 5 lines x 4 machines x 35 days x 3 shifts of production records,
a maintenance history per machine, and a quality inspection per
machine/shift/date, with one known incident running through all three
tables: Line 4 / machine M-104 / 2026-08-25 shows unusually low actual
production and much higher downtime than its own baseline and its
line-mates that day (production table), caused by a motor winding failure
recorded the same day (maintenance_events table) - the third in a string of
motor-related failures on M-104 - which also drove a spike in Assembly
Defect rejections on M-104's output that day (quality_inspections table).
No table has a root_cause/flag column, so the agent has to notice the
pattern the same way a human would: by comparing actual vs. planned/machine
vs. machine, by reading maintenance failure types and descriptions, and by
comparing rejection rates against history.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from app.database.auth_repository import DEFAULT_ACCOUNTS, create_user
from app.database.connection import SessionLocal, init_db
from app.database.models import (
    MaintenanceEvent,
    Machine,
    Production,
    ProductionLine,
    QualityInspection,
)

RNG_SEED = 20260825

LINES = [
    {"line_id": "LINE-1", "line_name": "Line 1 - Stamping", "department": "Metal Forming"},
    {"line_id": "LINE-2", "line_name": "Line 2 - Welding", "department": "Body Shop"},
    {"line_id": "LINE-3", "line_name": "Line 3 - Paint", "department": "Paint Shop"},
    {"line_id": "LINE-4", "line_name": "Line 4 - Assembly", "department": "Final Assembly"},
    {"line_id": "LINE-5", "line_name": "Line 5 - Packaging", "department": "Packaging & Shipping"},
]
MACHINE_TYPES = ["CNC Press", "Robotic Welder", "Paint Robot", "Conveyor Assembler", "Case Packer"]
TOTAL_MACHINES = 20  # 4 per line, round-robin assigned -> M-104 lands on LINE-4

SHIFTS = ["Day", "Evening", "Night"]
BASELINE_PLANNED_PER_MACHINE_SHIFT = {
    "LINE-1": 750,
    "LINE-2": 700,
    "LINE-3": 875,
    "LINE-4": 1000,
    "LINE-5": 800,
}

END_DATE = date(2026, 8, 25)
NUM_DAYS = 35
START_DATE = END_DATE - timedelta(days=NUM_DAYS - 1)

# Known incident: an equipment failure on M-104 (Line 4), worst in the
# Evening shift, still depressing Night shift output as repairs continue.
INCIDENT_DATE = date(2026, 8, 25)
INCIDENT_LINE = "LINE-4"
INCIDENT_MACHINE = "M-104"
INCIDENT_PROFILE = {
    "Day": {"actual": 550, "downtime": 90},
    "Evening": {"actual": 300, "downtime": 140},
    "Night": {"actual": 600, "downtime": 80},
}


def build_machines() -> list[dict]:
    machines = []
    for i in range(TOTAL_MACHINES):
        line = LINES[i % len(LINES)]
        machine_type = MACHINE_TYPES[i % len(LINES)]
        machine_id = f"M-{101 + i}"
        machines.append(
            {
                "machine_id": machine_id,
                "machine_name": f"{machine_type} {machine_id}",
                "line_id": line["line_id"],
                "machine_type": machine_type,
                "status": "running",
            }
        )
    return machines


def generate_production_rows(machines: list[dict]) -> list[dict]:
    rng = random.Random(RNG_SEED)
    rows = []
    current = START_DATE
    while current <= END_DATE:
        for machine in machines:
            baseline = BASELINE_PLANNED_PER_MACHINE_SHIFT[machine["line_id"]]
            for shift in SHIFTS:
                is_incident_slot = (
                    current == INCIDENT_DATE
                    and machine["line_id"] == INCIDENT_LINE
                    and machine["machine_id"] == INCIDENT_MACHINE
                )
                if is_incident_slot:
                    profile = INCIDENT_PROFILE[shift]
                    planned = baseline
                    actual = profile["actual"]
                    downtime = profile["downtime"]
                else:
                    planned = round(baseline * rng.uniform(0.97, 1.03))
                    actual = round(planned * rng.uniform(0.94, 0.99))
                    downtime = rng.randint(2, 12)

                rows.append(
                    {
                        "line_id": machine["line_id"],
                        "machine_id": machine["machine_id"],
                        "production_date": current,
                        "shift": shift,
                        "planned_quantity": planned,
                        "actual_quantity": actual,
                        "downtime_minutes": downtime,
                    }
                )
        current += timedelta(days=1)
    return rows


# --- Maintenance data (Step 5) -------------------------------------------

MAINTENANCE_RNG_SEED = 20260826  # separate from RNG_SEED so it never
# perturbs the (already-verified) production numbers from Step 4.

MAINTENANCE_WINDOW_START = date(2026, 6, 1)
MAINTENANCE_WINDOW_END = date(2026, 8, 25)

GENERIC_FAILURE_TYPES = [
    "Sensor Fault",
    "Belt Wear",
    "Hydraulic Leak",
    "Electrical Fault",
    "Conveyor Jam",
    "Calibration Drift",
]
TECHNICIANS = ["J. Alvarez", "R. Chen", "S. Okafor", "M. Petrov", "L. Nguyen"]

# M-104's history: two earlier motor-related failures (escalating severity)
# and an inspection that flagged an early warning sign, ending in the known
# 2026-08-25 incident. failure_type is deliberately the same category
# ("Motor Failure") each time so it's a genuinely recurring failure type,
# not just three differently-worded one-offs; the specific detail lives in
# `description`. The incident's downtime_minutes (310) matches the sum of
# M-104's production-table downtime that day (90 + 140 + 80) on purpose.
M104_MACHINE_ID = "M-104"
M104_MAINTENANCE_HISTORY = [
    {
        "maintenance_date": date(2026, 6, 15),
        "event_type": "Corrective",
        "failure_type": "Motor Failure",
        "description": (
            "Motor overheating detected during routine operation; winding "
            "inspected and cooling fan replaced."
        ),
        "downtime_minutes": 95,
        "technician": "J. Alvarez",
        "status": "Resolved",
    },
    {
        "maintenance_date": date(2026, 7, 2),
        "event_type": "Preventive",
        "failure_type": None,
        "description": "Scheduled preventive maintenance - lubrication and belt tension check.",
        "downtime_minutes": 30,
        "technician": "R. Chen",
        "status": "Resolved",
    },
    {
        "maintenance_date": date(2026, 7, 19),
        "event_type": "Corrective",
        "failure_type": "Motor Failure",
        "description": (
            "Drive motor bearing failure caused excessive vibration; bearing replaced."
        ),
        "downtime_minutes": 110,
        "technician": "J. Alvarez",
        "status": "Resolved",
    },
    {
        "maintenance_date": date(2026, 8, 5),
        "event_type": "Inspection",
        "failure_type": None,
        "description": (
            "Routine inspection - motor temperature slightly elevated, flagged for monitoring."
        ),
        "downtime_minutes": 15,
        "technician": "R. Chen",
        "status": "Resolved",
    },
    {
        "maintenance_date": date(2026, 8, 25),
        "event_type": "Corrective",
        "failure_type": "Motor Failure",
        "description": (
            "Drive motor winding failed during the Evening shift, causing severe "
            "output loss across Day/Evening/Night; motor replaced and line restarted."
        ),
        "downtime_minutes": 310,
        "technician": "J. Alvarez",
        "status": "Resolved",
    },
]


def generate_maintenance_events(machines: list[dict]) -> list[dict]:
    rng = random.Random(MAINTENANCE_RNG_SEED)
    window_days = (MAINTENANCE_WINDOW_END - MAINTENANCE_WINDOW_START).days
    events = []

    for machine in machines:
        if machine["machine_id"] == M104_MACHINE_ID:
            for record in M104_MAINTENANCE_HISTORY:
                events.append({"machine_id": machine["machine_id"], **record})
            continue

        used_offsets: set[int] = set()
        for _ in range(rng.randint(2, 4)):
            offset = rng.randint(0, window_days)
            while offset in used_offsets:
                offset = rng.randint(0, window_days)
            used_offsets.add(offset)
            event_date = MAINTENANCE_WINDOW_START + timedelta(days=offset)

            if rng.random() < 0.35:
                failure_type = rng.choice(GENERIC_FAILURE_TYPES)
                event_type = "Corrective"
                downtime = rng.randint(20, 60)
                description = f"{failure_type} on {machine['machine_id']}; resolved by maintenance."
            else:
                failure_type = None
                event_type = rng.choice(["Preventive", "Inspection"])
                downtime = rng.randint(10, 30)
                description = f"Routine {event_type.lower()} maintenance on {machine['machine_id']}."

            events.append(
                {
                    "machine_id": machine["machine_id"],
                    "maintenance_date": event_date,
                    "event_type": event_type,
                    "failure_type": failure_type,
                    "description": description,
                    "downtime_minutes": downtime,
                    "technician": rng.choice(TECHNICIANS),
                    "status": "Resolved",
                }
            )

    return events


# --- Quality data (Step 6) -------------------------------------------------

QUALITY_RNG_SEED = 20260827  # separate from the other seeds, same reason.

QUALITY_DEFECT_TYPES = ["Dimension Out of Tolerance", "Surface Defect", "Assembly Defect"]
QUALITY_DEFECT_DESCRIPTIONS = {
    "Dimension Out of Tolerance": "Measured dimension fell outside the spec tolerance band.",
    "Surface Defect": "Visible surface blemish or finish defect detected during inspection.",
    "Assembly Defect": "Component misalignment or incomplete assembly detected during inspection.",
}

# Normal-day rejection rate, as a fraction of inspected quantity.
NORMAL_REJECTION_RATE_RANGE = (0.005, 0.025)
# M-104's rejection rate on the incident day, by shift - inspected_quantity
# is still whatever that machine/shift actually produced (from the
# production table), only the rejection rate is elevated, and only on
# M-104 - Line 4's other machines that day use the normal random range like
# any other day, so the anomaly is a machine-level outlier, not a
# line-wide flag.
INCIDENT_QUALITY_REJECTION_RATE = {"Day": 0.10, "Evening": 0.25, "Night": 0.15}


def generate_quality_rows(machines: list[dict], production_rows: list[dict]) -> list[dict]:
    rng = random.Random(QUALITY_RNG_SEED)
    machines_by_id = {m["machine_id"]: m for m in machines}

    rows = []
    for prod_row in production_rows:
        inspected = prod_row["actual_quantity"]
        if inspected <= 0:
            continue
        machine = machines_by_id[prod_row["machine_id"]]

        is_incident_slot = (
            prod_row["production_date"] == INCIDENT_DATE
            and machine["line_id"] == INCIDENT_LINE
            and machine["machine_id"] == INCIDENT_MACHINE
        )
        if is_incident_slot:
            rate = INCIDENT_QUALITY_REJECTION_RATE[prod_row["shift"]]
            defect_type = "Assembly Defect"
        else:
            rate = rng.uniform(*NORMAL_REJECTION_RATE_RANGE)
            if rng.random() < 0.15:
                rate = 0.0
            defect_type = rng.choice(QUALITY_DEFECT_TYPES) if rate > 0 else None

        rejected = round(inspected * rate)
        if rejected <= 0:
            defect_type = None

        rows.append(
            {
                "line_id": machine["line_id"],
                "machine_id": machine["machine_id"],
                "inspection_date": prod_row["production_date"],
                "shift": prod_row["shift"],
                "inspected_quantity": inspected,
                "rejected_quantity": rejected,
                "defect_type": defect_type,
                "defect_description": (
                    QUALITY_DEFECT_DESCRIPTIONS[defect_type] if defect_type else None
                ),
            }
        )

    return rows


def seed() -> None:
    machines = build_machines()
    rows = generate_production_rows(machines)
    maintenance_events = generate_maintenance_events(machines)
    quality_rows = generate_quality_rows(machines, rows)

    with SessionLocal() as session:
        line_objs = {}
        for line in LINES:
            obj = ProductionLine(**line)
            session.add(obj)
            line_objs[line["line_id"]] = obj
        session.flush()

        machine_objs = {}
        for m in machines:
            obj = Machine(
                machine_id=m["machine_id"],
                machine_name=m["machine_name"],
                line_id=line_objs[m["line_id"]].id,
                machine_type=m["machine_type"],
                status=m["status"],
            )
            session.add(obj)
            machine_objs[m["machine_id"]] = obj
        session.flush()

        for row in rows:
            session.add(
                Production(
                    line_id=line_objs[row["line_id"]].id,
                    machine_id=machine_objs[row["machine_id"]].id,
                    production_date=row["production_date"],
                    shift=row["shift"],
                    planned_quantity=row["planned_quantity"],
                    actual_quantity=row["actual_quantity"],
                    downtime_minutes=row["downtime_minutes"],
                )
            )

        for event in maintenance_events:
            session.add(
                MaintenanceEvent(
                    machine_id=machine_objs[event["machine_id"]].id,
                    maintenance_date=event["maintenance_date"],
                    event_type=event["event_type"],
                    failure_type=event["failure_type"],
                    description=event["description"],
                    downtime_minutes=event["downtime_minutes"],
                    technician=event["technician"],
                    status=event["status"],
                )
            )

        for q in quality_rows:
            session.add(
                QualityInspection(
                    line_id=line_objs[q["line_id"]].id,
                    machine_id=machine_objs[q["machine_id"]].id,
                    inspection_date=q["inspection_date"],
                    shift=q["shift"],
                    inspected_quantity=q["inspected_quantity"],
                    rejected_quantity=q["rejected_quantity"],
                    defect_type=q["defect_type"],
                    defect_description=q["defect_description"],
                )
            )

        for username, password, role in DEFAULT_ACCOUNTS:
            create_user(session, username, password, role)

        session.commit()

    print(
        f"[seed] {len(LINES)} lines, {len(machines)} machines, "
        f"{NUM_DAYS} days x {len(SHIFTS)} shifts = {len(rows)} production records "
        f"({START_DATE.isoformat()} to {END_DATE.isoformat()})."
    )
    print(f"[seed] {len(maintenance_events)} maintenance events ({MAINTENANCE_WINDOW_START.isoformat()} to {MAINTENANCE_WINDOW_END.isoformat()}).")
    print(f"[seed] {len(quality_rows)} quality inspections.")
    print(f"[seed] Known incident: {INCIDENT_LINE} / {INCIDENT_MACHINE} / {INCIDENT_DATE.isoformat()}")
    print(f"[seed]   -> {len(M104_MAINTENANCE_HISTORY)} maintenance events on {M104_MACHINE_ID}, 3 of them 'Motor Failure'.")
    print(f"[seed]   -> elevated Assembly Defect rejection rate on {M104_MACHINE_ID} that day (Evening worst).")
    print("[seed] Login accounts (demo only - override via SEED_ADMIN_PASSWORD/SEED_USER_PASSWORD):")
    for username, password, role in DEFAULT_ACCOUNTS:
        print(f"[seed]   -> {username} / {password}  (role={role})")


def main() -> None:
    print("[seed] Resetting schema...")
    init_db(drop_existing=True)
    seed()


if __name__ == "__main__":
    main()
