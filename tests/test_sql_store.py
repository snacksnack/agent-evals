"""`SqlRunStore` against a real Postgres.

These tests need a database and skip without one. CI provides a `postgres:16`
service container — a real Postgres, triggers included, with no secret and no
vendor, which is the property ADR-0031 protects. To run them locally:

    docker run --rm -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16
    EVAL_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \\
        uv run pytest tests/test_sql_store.py

The load-bearing tests are the UPDATE and DELETE refusals: the reason this
store exists is that the *database* enforces append-only (RC1-263), rather
than the class merely not offering mutation the way the JSONL store cannot
help doing.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from agent_evals.record import (
    CaseResult,
    CharacteristicResult,
    CredentialShapedRecord,
    DuplicateRunId,
    RunRecord,
    SubjectVersion,
    Usage,
)
from agent_evals.sql_store import SqlRunStore

DSN = os.environ.get("EVAL_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DSN, reason="EVAL_TEST_DATABASE_URL is not set — CI provides a service container"
)

_WHEN = datetime(2026, 8, 13, 17, 42, 33, tzinfo=UTC)


def _record(
    run_id: str = "health-20260813T174233.000000Z",
    *,
    subject: str = "health",
    started_at: datetime = _WHEN,
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        subject_version=SubjectVersion(subject=subject, code_version="0.2.0"),
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=1),
        results=[
            CaseResult(
                case_id="c1",
                characteristics=[CharacteristicResult(name="a", passed=True, detail="because")],
                usage=Usage(latency_ms=12.5, cost_usd=Decimal("0.0000031")),
            )
        ],
    )


@pytest.fixture
def store():
    # sslmode="prefer" rather than the "require" default: the CI service
    # container speaks no TLS, and the trigger under test behaves identically.
    s = SqlRunStore(DSN, sslmode="prefer")
    conn = s._connection()
    with conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS eval_runs")
    s.ensure_schema()
    yield s
    s.close()


def test_append_and_read_back(store):
    store.append(_record())
    read = store.get("health-20260813T174233.000000Z")
    assert read.subject_version.subject == "health"
    assert store.get("missing") is None


def test_cost_stays_exact_across_the_round_trip(store):
    """Decimal survives JSONB: the cost is a JSON string, so Postgres never
    touches it as a number."""
    store.append(_record())
    read = store.get("health-20260813T174233.000000Z")
    assert read.results[0].usage.cost_usd == Decimal("0.0000031")


def test_duplicate_run_ids_are_rejected(store):
    """The unique key does what the JSONL store's read-before-append does."""
    store.append(_record())
    with pytest.raises(DuplicateRunId):
        store.append(_record())


def test_update_is_refused_by_the_database(store):
    """Acceptance criterion (RC1-263). Raw SQL, not the class API: the point is
    that mutation fails even for a caller that goes around `SqlRunStore`."""
    import psycopg2

    store.append(_record())
    conn = store._connection()
    with (
        pytest.raises(psycopg2.errors.RaiseException, match="append-only"),
        conn,
        conn.cursor() as cur,
    ):
        cur.execute("UPDATE eval_runs SET subject = 'tampered'")


def test_delete_is_refused_by_the_database(store):
    import psycopg2

    store.append(_record())
    conn = store._connection()
    with (
        pytest.raises(psycopg2.errors.RaiseException, match="append-only"),
        conn,
        conn.cursor() as cur,
    ):
        cur.execute("DELETE FROM eval_runs")


def test_all_returns_oldest_first(store):
    """The same contract as the JSONL store, so the two are interchangeable."""
    store.append(_record("health-20260813T174234.000000Z", started_at=_WHEN + timedelta(seconds=1)))
    store.append(_record("health-20260813T174233.000000Z"))
    assert [r.run_id for r in store.all()] == [
        "health-20260813T174233.000000Z",
        "health-20260813T174234.000000Z",
    ]


def test_latest_filters_by_subject(store):
    store.append(_record("health-20260813T174233.000000Z"))
    store.append(
        _record(
            "other-20260813T174234.000000Z",
            subject="other",
            started_at=_WHEN + timedelta(seconds=1),
        )
    )
    assert store.latest().run_id == "other-20260813T174234.000000Z"
    assert store.latest("health").run_id == "health-20260813T174233.000000Z"
    assert store.latest("nobody") is None


def test_raw_dicts_feed_the_trend_view(store):
    """`trend.points` works off raw dicts — the seam RC1-263 must not move."""
    from agent_evals import trend

    store.append(_record())
    points = trend.points(store.raw())
    assert len(points) == 1
    assert points[0].subject == "health"


def test_a_credential_shaped_record_is_refused(store):
    """The write-time guard (RC1-263), verified by planting one. The value is
    assembled at runtime so this file never contains a scannable credential."""
    record = _record()
    record.results[0].characteristics[0] = CharacteristicResult(
        name="leak", passed=False, detail="found " + "sk_live_" + "a" * 24
    )
    with pytest.raises(CredentialShapedRecord, match="drop this record"):
        store.append(record)
    assert store.all() == []


def test_ensure_schema_is_idempotent(store):
    store.append(_record())
    store.ensure_schema()
    assert len(store.all()) == 1
