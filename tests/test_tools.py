"""Tests for the pure data-access layer behind every tool: the PostgreSQL
repositories (Steps 4-6) and the RAG pipeline's chunking/search (Step 7).

None of this exercises the Claude Agent SDK - these are the modules a tool
(app.tools.*) calls into, tested directly. Real database/Qdrant calls, no
mocking - skipped (not faked) when the relevant service isn't reachable, so
the suite still runs cleanly on a machine that hasn't started the
containers.

Requires, for the DB tests:
    docker compose up -d
    python -m app.database.seed

And for the Qdrant tests:
    docker compose up -d
    python -m app.rag.ingest
"""

from __future__ import annotations

import asyncio
import json
import unittest

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.database import maintenance_repository
from app.database import production_repository as repository
from app.database import quality_repository
from app.database.connection import _normalize_database_url, engine
from app.rag.ingest import Document, chunk_document, chunk_documents, get_client, load_documents
from app.rag.search import search
from app.tools import maintenance_tools, production_tools, quality_tools


def _database_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


def _qdrant_ready() -> bool:
    try:
        get_client().get_collections()
        return True
    except Exception:
        return False


class DatabaseUrlNormalizationTests(unittest.TestCase):
    """DATABASE_URL support (for managed providers like Neon) - a pure
    string transform, so no live DB needed. Verified separately against a
    real Neon database when this was added; this just locks the behavior.
    """

    def test_rewrites_postgresql_scheme_for_the_psycopg3_driver(self) -> None:
        raw = "postgresql://user:pass@host/db?sslmode=require&channel_binding=require"
        self.assertEqual(
            _normalize_database_url(raw),
            "postgresql+psycopg://user:pass@host/db?sslmode=require&channel_binding=require",
        )

    def test_rewrites_the_short_postgres_scheme_too(self) -> None:
        self.assertEqual(_normalize_database_url("postgres://u:p@h/d"), "postgresql+psycopg://u:p@h/d")

    def test_leaves_an_already_qualified_scheme_untouched(self) -> None:
        raw = "postgresql+psycopg://u:p@h/d"
        self.assertEqual(_normalize_database_url(raw), raw)


@unittest.skipUnless(
    _database_reachable(),
    "PostgreSQL not reachable - run `docker compose up -d` and seed the database first",
)
class ProductionRepositoryTests(unittest.TestCase):
    def test_unknown_line_or_date_returns_none(self) -> None:
        self.assertIsNone(repository.get_production_metrics("Line 99", "2026-08-25"))
        self.assertIsNone(repository.get_production_metrics("Line 4", "1999-01-01"))

    def test_line_4_incident_on_2026_08_25_is_computed_not_hardcoded(self) -> None:
        metrics = repository.get_production_metrics("Line 4", "2026-08-25")
        assert metrics is not None

        self.assertEqual(metrics["line_id"], "LINE-4")
        # ~12000 (4 machines x 1000/shift x 3 shifts), +/- the seed script's
        # deliberate +/-3% realism jitter on non-incident machines.
        self.assertAlmostEqual(metrics["planned_quantity"], 12000, delta=500)
        self.assertGreater(metrics["production_loss_percentage"], 10.0)

        shift_names = {s["shift"] for s in metrics["shift_breakdown"]}
        self.assertEqual(shift_names, {"Day", "Evening", "Night"})

        evening = next(s for s in metrics["shift_breakdown"] if s["shift"] == "Evening")
        m104 = next(m for m in evening["machines"] if m["machine_id"] == "M-104")
        self.assertEqual(m104["actual_quantity"], 300)
        self.assertEqual(m104["downtime_minutes"], 140)
        self.assertGreater(m104["production_loss_percentage"], 50.0)

        # M-104 should be the clear outlier among Line 4's machines that shift.
        other_losses = [
            m["production_loss_percentage"] for m in evening["machines"] if m["machine_id"] != "M-104"
        ]
        self.assertTrue(all(m104["production_loss_percentage"] > loss for loss in other_losses))

    def test_line_4_normal_day_has_low_loss(self) -> None:
        metrics = repository.get_production_metrics("LINE-4", "2026-08-10")
        assert metrics is not None
        self.assertLess(metrics["production_loss_percentage"], 10.0)

    def test_accepts_alternate_line_id_spelling(self) -> None:
        by_name = repository.get_production_metrics("Line 4", "2026-08-25")
        by_id = repository.get_production_metrics("LINE-4", "2026-08-25")
        self.assertEqual(by_name, by_id)

    def test_all_five_lines_have_data(self) -> None:
        self.assertEqual(repository.available_line_ids(), [f"LINE-{i}" for i in range(1, 6)])


@unittest.skipUnless(
    _database_reachable(),
    "PostgreSQL not reachable - run `docker compose up -d` and seed the database first",
)
class ProductionBatchToolTests(unittest.TestCase):
    """get_production_metrics_batch (the tool layer, not just the
    repository): calling the real @tool-decorated handler directly, with no
    Claude Agent SDK session involved, proves the batching/concurrency and
    capability-gate behavior without needing a live LLM call. Real Postgres,
    no mocking - same policy as every other test in this file.
    """

    def test_batch_returns_one_result_per_pair_in_request_order(self) -> None:
        result = asyncio.run(
            production_tools.get_production_metrics_batch.handler(
                {
                    "requests": [
                        {"line_id": "Line 4", "date": "2026-08-25"},
                        {"line_id": "Line 4", "date": "2026-08-10"},
                    ]
                }
            )
        )
        self.assertFalse(result.get("is_error", False))
        payload = json.loads(result["content"][0]["text"])
        results = payload["results"]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["date"], "2026-08-25")
        self.assertEqual(results[1]["date"], "2026-08-10")
        # Matches the single-item tool's own numbers for the same pair -
        # batching must not change the answer, only how many round trips
        # it takes to get it.
        single = repository.get_production_metrics("Line 4", "2026-08-25")
        self.assertEqual(results[0]["planned_quantity"], single["planned_quantity"])

    def test_batch_reports_unknown_pairs_without_failing_the_whole_call(self) -> None:
        result = asyncio.run(
            production_tools.get_production_metrics_batch.handler(
                {
                    "requests": [
                        {"line_id": "Line 4", "date": "2026-08-25"},
                        {"line_id": "Line 99", "date": "2026-08-25"},
                    ]
                }
            )
        )
        self.assertFalse(result.get("is_error", False))
        results = json.loads(result["content"][0]["text"])["results"]
        self.assertNotIn("error", results[0])
        self.assertIn("error", results[1])

    def test_batch_requires_at_least_one_request(self) -> None:
        result = asyncio.run(production_tools.get_production_metrics_batch.handler({"requests": []}))
        self.assertTrue(result.get("is_error"))


@unittest.skipUnless(
    _database_reachable(),
    "PostgreSQL not reachable - run `docker compose up -d` and seed the database first",
)
class QualityBatchToolTests(unittest.TestCase):
    """get_quality_metrics_batch: same rationale/shape as
    ProductionBatchToolTests above - added after a live run of an
    ambiguous-date question ("why did rejection increase?") was observed
    making 7 sequential get_quality_metrics calls, one per candidate date.
    """

    def test_batch_returns_one_result_per_pair_in_request_order(self) -> None:
        result = asyncio.run(
            quality_tools.get_quality_metrics_batch.handler(
                {
                    "requests": [
                        {"line_id": "Line 4", "date": "2026-08-25"},
                        {"line_id": "Line 4", "date": "2026-08-10"},
                    ]
                }
            )
        )
        self.assertFalse(result.get("is_error", False))
        results = json.loads(result["content"][0]["text"])["results"]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["date"], "2026-08-25")
        single = quality_repository.get_quality_metrics("Line 4", "2026-08-25")
        self.assertEqual(results[0]["rejection_percentage"], single["rejection_percentage"])

    def test_batch_reports_unknown_pairs_without_failing_the_whole_call(self) -> None:
        result = asyncio.run(
            quality_tools.get_quality_metrics_batch.handler(
                {
                    "requests": [
                        {"line_id": "Line 4", "date": "2026-08-25"},
                        {"line_id": "Line 99", "date": "2026-08-25"},
                    ]
                }
            )
        )
        self.assertFalse(result.get("is_error", False))
        results = json.loads(result["content"][0]["text"])["results"]
        self.assertNotIn("error", results[0])
        self.assertIn("error", results[1])

    def test_batch_requires_at_least_one_request(self) -> None:
        result = asyncio.run(quality_tools.get_quality_metrics_batch.handler({"requests": []}))
        self.assertTrue(result.get("is_error"))


@unittest.skipUnless(
    _database_reachable(),
    "PostgreSQL not reachable - run `docker compose up -d` and seed the database first",
)
class MaintenanceRepositoryTests(unittest.TestCase):
    def test_unknown_machine_returns_none(self) -> None:
        self.assertIsNone(maintenance_repository.get_machine_downtime("M-999", "2026-08-25"))
        self.assertIsNone(maintenance_repository.get_maintenance_events("M-999", "2026-01-01", "2026-12-31"))
        self.assertIsNone(maintenance_repository.get_machine_history("M-999"))

    def test_machine_downtime_on_incident_day(self) -> None:
        result = maintenance_repository.get_machine_downtime("M-104", "2026-08-25")
        assert result is not None

        self.assertEqual(result["machine_id"], "M-104")
        self.assertEqual(result["downtime_minutes"], 310)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["failure_type"], "Motor Failure")
        self.assertEqual(result["affected_shift"], "Evening")

    def test_machine_downtime_on_quiet_day_is_zero_not_missing(self) -> None:
        result = maintenance_repository.get_machine_downtime("M-104", "2026-07-10")
        assert result is not None
        self.assertEqual(result["downtime_minutes"], 0)
        self.assertEqual(result["events"], [])

    def test_maintenance_events_date_range(self) -> None:
        result = maintenance_repository.get_maintenance_events("M-104", "2026-06-01", "2026-08-25")
        assert result is not None
        self.assertEqual(len(result["events"]), 5)
        self.assertEqual(result["total_downtime_minutes"], 95 + 30 + 110 + 15 + 310)

    def test_machine_history_finds_recurring_failure_type(self) -> None:
        result = maintenance_repository.get_machine_history("M-104")
        assert result is not None

        self.assertEqual(result["total_events"], 5)
        self.assertEqual(result["failure_event_count"], 3)
        self.assertEqual(result["failure_type_counts"].get("Motor Failure"), 3)
        self.assertIn("Motor Failure", result["recurring_failure_types"])

    def test_accepts_alternate_machine_id_spelling(self) -> None:
        by_hyphen = maintenance_repository.get_machine_history("M-104")
        by_plain = maintenance_repository.get_machine_history("104")
        self.assertEqual(by_hyphen, by_plain)

    def test_line_downtime_unknown_line_returns_none(self) -> None:
        self.assertIsNone(maintenance_repository.get_line_downtime("Line 99", "2026-08-25"))

    def test_line_downtime_finds_the_affected_machine_in_one_call(self) -> None:
        # This is the whole point of the tool: answer "which machine on
        # this line went down" without checking machines one at a time.
        result = maintenance_repository.get_line_downtime("Line 4", "2026-08-25")
        assert result is not None
        self.assertEqual(result["line_id"], "LINE-4")

        machine_ids = [m["machine_id"] for m in result["machines"]]
        self.assertIn("M-104", machine_ids)
        # Sorted worst-downtime-first, so the incident machine leads.
        self.assertEqual(result["machines"][0]["machine_id"], "M-104")
        self.assertEqual(result["machines"][0]["downtime_minutes"], 310)
        self.assertEqual(result["machines"][0]["affected_shift"], "Evening")

    def test_line_downtime_on_quiet_day_returns_every_machine_at_zero(self) -> None:
        result = maintenance_repository.get_line_downtime("Line 4", "2026-07-10")
        assert result is not None
        self.assertGreaterEqual(len(result["machines"]), 1)
        self.assertTrue(all(m["downtime_minutes"] == 0 for m in result["machines"]))

    def test_line_downtime_matches_per_machine_downtime_lookup(self) -> None:
        by_line = maintenance_repository.get_line_downtime("Line 4", "2026-08-25")
        by_machine = maintenance_repository.get_machine_downtime("M-104", "2026-08-25")
        assert by_line is not None and by_machine is not None

        m104 = next(m for m in by_line["machines"] if m["machine_id"] == "M-104")
        self.assertEqual(m104["downtime_minutes"], by_machine["downtime_minutes"])
        self.assertEqual(m104["events"], by_machine["events"])


@unittest.skipUnless(
    _database_reachable(),
    "PostgreSQL not reachable - run `docker compose up -d` and seed the database first",
)
class MaintenanceLineDowntimeBatchToolTests(unittest.TestCase):
    """get_line_downtime_batch: same rationale/shape as
    ProductionBatchToolTests/QualityBatchToolTests above - added after a
    live run of an ambiguous-date question made 6 sequential
    get_line_downtime calls, one per candidate date.
    """

    def test_batch_returns_one_result_per_pair_in_request_order(self) -> None:
        result = asyncio.run(
            maintenance_tools.get_line_downtime_batch.handler(
                {
                    "requests": [
                        {"line_id": "Line 4", "date": "2026-08-25"},
                        {"line_id": "Line 4", "date": "2026-08-10"},
                    ]
                }
            )
        )
        self.assertFalse(result.get("is_error", False))
        results = json.loads(result["content"][0]["text"])["results"]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["date"], "2026-08-25")
        single = maintenance_repository.get_line_downtime("Line 4", "2026-08-25")
        self.assertEqual(results[0]["machines"], single["machines"])

    def test_batch_reports_unknown_pairs_without_failing_the_whole_call(self) -> None:
        result = asyncio.run(
            maintenance_tools.get_line_downtime_batch.handler(
                {
                    "requests": [
                        {"line_id": "Line 4", "date": "2026-08-25"},
                        {"line_id": "Line 99", "date": "2026-08-25"},
                    ]
                }
            )
        )
        self.assertFalse(result.get("is_error", False))
        results = json.loads(result["content"][0]["text"])["results"]
        self.assertNotIn("error", results[0])
        self.assertIn("error", results[1])

    def test_batch_requires_at_least_one_request(self) -> None:
        result = asyncio.run(maintenance_tools.get_line_downtime_batch.handler({"requests": []}))
        self.assertTrue(result.get("is_error"))


@unittest.skipUnless(
    _database_reachable(),
    "PostgreSQL not reachable - run `docker compose up -d` and seed the database first",
)
class QualityRepositoryTests(unittest.TestCase):
    def test_unknown_line_returns_none(self) -> None:
        self.assertIsNone(quality_repository.get_quality_metrics("Line 99", "2026-08-25"))
        self.assertIsNone(quality_repository.get_defect_distribution("Line 99", "2026-08-25"))
        self.assertIsNone(quality_repository.compare_quality_history("Line 99", "2026-08-25", 14))

    def test_quality_metrics_on_incident_day_shows_elevated_rejection(self) -> None:
        # Line-wide average is diluted (only 1 of Line 4's 4 machines is
        # anomalous that day), so this checks it's clearly above the ~1.3%
        # historical baseline, not an arbitrary large threshold - the sharp
        # signal is confirmed separately at the trend/machine level below.
        metrics = quality_repository.get_quality_metrics("Line 4", "2026-08-25")
        assert metrics is not None
        self.assertEqual(metrics["line_id"], "LINE-4")
        self.assertGreater(metrics["rejection_percentage"], 2.5)
        self.assertTrue(metrics["defect_summary"])
        top_defect = metrics["defect_summary"][0]
        self.assertEqual(top_defect["defect_type"], "Assembly Defect")

    def test_quality_metrics_on_normal_day_is_low(self) -> None:
        metrics = quality_repository.get_quality_metrics("Line 4", "2026-08-10")
        assert metrics is not None
        self.assertLess(metrics["rejection_percentage"], 5.0)

    def test_defect_distribution_sums_to_total(self) -> None:
        dist = quality_repository.get_defect_distribution("Line 4", "2026-08-25")
        assert dist is not None
        summed = sum(d["rejected_quantity"] for d in dist["defect_types"])
        self.assertEqual(summed, dist["total_rejected_quantity"])
        percentages = sum(d["percentage_of_total_defects"] for d in dist["defect_types"])
        self.assertAlmostEqual(percentages, 100.0, delta=0.1)

    def test_compare_quality_history_flags_incident_as_above_normal(self) -> None:
        comparison = quality_repository.compare_quality_history("Line 4", "2026-08-25", 14)
        assert comparison is not None
        self.assertGreater(comparison["current_rejection_percentage"], comparison["historical_average_rejection_percentage"])
        self.assertIn(comparison["trend"], ("above_normal", "significantly_above_normal"))
        self.assertGreater(comparison["historical_days_with_data"], 0)

    def test_compare_quality_history_normal_day_is_not_flagged(self) -> None:
        comparison = quality_repository.compare_quality_history("Line 4", "2026-08-10", 14)
        assert comparison is not None
        self.assertEqual(comparison["trend"], "normal")

    def test_invalid_lookback_days_returns_none(self) -> None:
        self.assertIsNone(quality_repository.compare_quality_history("Line 4", "2026-08-25", 0))
        self.assertIsNone(quality_repository.compare_quality_history("Line 4", "2026-08-25", -5))

    def test_accepts_alternate_line_id_spelling(self) -> None:
        by_name = quality_repository.get_quality_metrics("Line 4", "2026-08-25")
        by_id = quality_repository.get_quality_metrics("LINE-4", "2026-08-25")
        self.assertEqual(by_name, by_id)


class ChunkingTests(unittest.TestCase):
    def test_chunk_document_splits_on_section_headers(self) -> None:
        doc = Document(
            name="sample.md",
            path="data/documents/sample.md",
            text=(
                "# Sample Doc\n\n"
                "Intro text.\n\n"
                "## Section A\n\nContent for A.\n\n"
                "## Section B\n\nContent for B.\n"
            ),
        )
        chunks = chunk_document(doc)

        self.assertGreaterEqual(len(chunks), 2)
        section_titles = {c.section_title for c in chunks}
        self.assertIn("Section A", section_titles)
        self.assertIn("Section B", section_titles)
        for c in chunks:
            self.assertEqual(c.document_name, "sample.md")

    def test_long_section_is_split_with_overlap(self) -> None:
        long_section = "## Big Section\n\n" + "\n\n".join(f"Paragraph {i} with some content." for i in range(50))
        doc = Document(name="big.md", path="x", text=f"# Big\n\n{long_section}")

        chunks = chunk_document(doc, size=200, overlap=40)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(c.section_title == "Big Section" for c in chunks))

    def test_chunk_documents_loads_real_files(self) -> None:
        documents = load_documents()
        self.assertEqual(len(documents), 4)
        names = {d.name for d in documents}
        self.assertEqual(
            names,
            {
                "maintenance_manual.md",
                "motor_failure_sop.md",
                "quality_procedure.md",
                "line4_procedure.md",
            },
        )

        chunks = chunk_documents(documents)
        self.assertGreater(len(chunks), len(documents))


@unittest.skipUnless(
    _qdrant_ready(),
    "Qdrant not reachable - run `docker compose up -d` and `python -m app.rag.ingest` first",
)
class QdrantSearchTests(unittest.TestCase):
    def test_motor_failure_query_retrieves_motor_failure_sop(self) -> None:
        results = search("What does the motor failure SOP recommend when a motor failure occurs?", top_k=5)

        self.assertTrue(results, "expected at least one result from Qdrant")
        self.assertEqual(results[0].document_name, "motor_failure_sop.md")
        self.assertGreater(results[0].score, 0.5)
        for r in results:
            self.assertTrue(r.text.strip())
            self.assertTrue(r.document_path.startswith("data/documents/"))

    def test_preventive_maintenance_query_retrieves_relevant_docs(self) -> None:
        results = search("preventive maintenance scheduling procedure", top_k=5)

        self.assertTrue(results)
        doc_names = {r.document_name for r in results}
        self.assertTrue(doc_names & {"maintenance_manual.md", "line4_procedure.md"})

    def test_unrelated_query_still_returns_a_low_confidence_top_result(self) -> None:
        # Cosine search always returns *something* - it's the tool/agent's
        # job to judge relevance, not the search call itself.
        results = search("what is the capital of France", top_k=3)
        self.assertTrue(results)


if __name__ == "__main__":
    unittest.main()
