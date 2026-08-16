"""The shared runner plumbing (RC1-262).

The store-dispatch test stubs `SqlRunStore` rather than connecting: which
store gets picked is this module's logic; whether Postgres works is
`test_sql_store.py`'s job against the service container.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_evals import runner
from agent_evals.record import CaseResult, CharacteristicResult, RunStore, SubjectVersion, Usage
from agent_evals.runner import (
    UnknownCase,
    exit_code,
    print_result,
    record_run,
    select_cases,
    store_from_env,
)

_WHEN = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)


class _Case:
    def __init__(self, id: str) -> None:
        self.id = id


def _result(
    case_id: str = "c1", *, failing: bool = False, advisory: bool = False, error: str | None = None
) -> CaseResult:
    characteristics = []
    if error is None:
        characteristics = [
            CharacteristicResult(
                name="a", passed=not failing, detail="because", advisory=advisory
            )
        ]
    return CaseResult(
        case_id=case_id,
        characteristics=characteristics,
        usage=Usage(latency_ms=2500.0),
        error=error,
    )


def test_select_cases_passes_everything_through_without_a_flag():
    cases = (_Case("a"), _Case("b"))
    assert select_cases(cases, None) == cases


def test_select_cases_narrows_to_the_named_id():
    cases = (_Case("a"), _Case("b"))
    assert [c.id for c in select_cases(cases, "b")] == ["b"]


def test_select_cases_raises_on_an_unknown_id():
    """A mistyped id that silently ran zero cases would exit 0 and read as a
    pass — the failure mode this exists to prevent."""
    with pytest.raises(UnknownCase, match="no case 'typo'"):
        select_cases((_Case("a"),), "typo")


def test_exit_codes_are_ci_shaped():
    assert exit_code([_result()]) == 0
    assert exit_code([_result(), _result(failing=True)]) == 1
    assert exit_code([_result(failing=True), _result(error="boom")]) == 2


def test_an_advisory_failure_does_not_move_the_exit_code():
    assert exit_code([_result(failing=True, advisory=True)]) == 0


def test_print_result_stays_quiet_about_passing_characteristics(capsys):
    print_result(_result())
    out = capsys.readouterr().out
    assert "pass c1  (2s)" in out
    assert "because" not in out


def test_print_result_shows_failures_and_advisories(capsys):
    print_result(_result(failing=True))
    print_result(_result(failing=True, advisory=True))
    out = capsys.readouterr().out
    assert "FAIL c1" in out
    assert "✗ a: because" in out
    assert "~ a [advisory]: because" in out


def test_print_result_reports_an_error_instead_of_a_score(capsys):
    print_result(_result(error="RuntimeError: down"))
    assert "ERROR c1: RuntimeError: down" in capsys.readouterr().out


def test_print_result_extends_the_header_with_extra(capsys):
    print_result(_result(), extra="3 finding(s), 1 off-target")
    assert "(2s, 3 finding(s), 1 off-target)" in capsys.readouterr().out


def test_record_run_appends_and_announces(tmp_path, capsys):
    store = RunStore(tmp_path / "runs.jsonl")
    version = SubjectVersion(subject="health", code_version="0.3.0")
    record = record_run(version, _WHEN, [_result()], store=store)

    assert record.run_id.startswith("health-")
    assert record.finished_at >= record.started_at - timedelta(seconds=0)
    assert store.get(record.run_id) is not None
    assert f"run {record.run_id} recorded" in capsys.readouterr().out


def test_store_from_env_defaults_to_the_local_jsonl(monkeypatch):
    monkeypatch.delenv("EVAL_DATABASE_URL", raising=False)
    monkeypatch.delenv("EVAL_RUNS_PATH", raising=False)
    store = store_from_env()
    assert isinstance(store, RunStore)
    assert store.path == Path("./eval-runs/runs.jsonl")


def test_store_from_env_respects_eval_runs_path(monkeypatch, tmp_path):
    monkeypatch.delenv("EVAL_DATABASE_URL", raising=False)
    monkeypatch.setenv("EVAL_RUNS_PATH", str(tmp_path / "other.jsonl"))
    assert store_from_env().path == tmp_path / "other.jsonl"


def test_an_explicit_default_path_beats_the_env_default(monkeypatch, tmp_path):
    """launch-planner resolves its own path (LPA_ settings); the helper must
    not override a consumer that already decided."""
    monkeypatch.delenv("EVAL_DATABASE_URL", raising=False)
    assert store_from_env(tmp_path / "mine.jsonl").path == tmp_path / "mine.jsonl"


def test_store_from_env_picks_postgres_when_the_dsn_is_set(monkeypatch):
    """Dispatch only — the stub records what it was handed. Postgres itself is
    tested in test_sql_store.py."""
    created = {}

    class _Stub:
        def __init__(self, dsn):
            created["dsn"] = dsn

        def ensure_schema(self):
            created["schema"] = True

    monkeypatch.setattr(runner, "SqlRunStore", _Stub)
    monkeypatch.setenv("EVAL_DATABASE_URL", "postgres://example/db")
    store = store_from_env()
    assert isinstance(store, _Stub)
    assert created == {"dsn": "postgres://example/db", "schema": True}
