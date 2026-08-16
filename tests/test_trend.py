"""The trend view, including the two things it must not get wrong (RC1-255).

It exists to answer one question — a score moved, what moved with it — so the
tests that matter are: does it attribute correctly, and does it refuse to lie
about what it is showing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_evals import trend

_WHEN = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)


def _record(n, *, passed=2, failed=0, model="claude-sonnet-5", prompt="p-1", code="1.0.0",
            advisory_fail=0, errored=0, cost="0"):
    results = []
    for i in range(passed):
        results.append({
            "case_id": f"ok-{i}",
            "characteristics": [{"name": "gates", "passed": True, "advisory": False}],
            "usage": {"cost_usd": cost, "latency_ms": 100.0},
        })
    for i in range(failed):
        results.append({
            "case_id": f"bad-{i}",
            "characteristics": [{"name": "gates", "passed": False, "advisory": False}],
            "usage": {"cost_usd": cost, "latency_ms": 100.0},
        })
    for i in range(advisory_fail):
        results.append({
            "case_id": f"adv-{i}",
            "characteristics": [
                {"name": "gates", "passed": True, "advisory": False},
                {"name": "soft", "passed": False, "advisory": True},
            ],
            "usage": {"cost_usd": cost, "latency_ms": 100.0},
        })
    for i in range(errored):
        results.append({"case_id": f"err-{i}", "error": "boom", "usage": {"latency_ms": 1.0}})
    return {
        "run_id": f"run-{n}",
        "subject_version": {
            "subject": "demo", "model": model, "prompt_version": prompt, "code_version": code,
        },
        "started_at": (_WHEN + timedelta(hours=n)).isoformat(),
        "finished_at": (_WHEN + timedelta(hours=n, minutes=1)).isoformat(),
        "results": results,
    }


def test_an_advisory_failure_never_counts_against_the_pass_rate():
    """The rule ADR-0033/0034 established, enforced at the point of display.

    A renderer that folded advisory results in would draw a subject as
    regressing on a dimension explicitly declared unable to gate — which is the
    same mistake as gating on it, made one layer later.
    """
    [point] = trend.points([_record(0, passed=1, advisory_fail=1)])
    assert point.rate == 1.0, "an advisory failure is not a regression"
    assert point.advisory_failed == 1, "...but it is still reported"


def test_an_errored_case_is_not_counted_as_passing():
    [point] = trend.points([_record(0, passed=1, errored=1)])
    assert point.passed == 1 and point.errored == 1
    assert point.rate == 0.5


def test_a_case_with_no_characteristics_does_not_pass_by_default():
    """Vacuous truth is the classic way a suite goes green for the wrong reason."""
    record = _record(0, passed=0)
    record["results"] = [{"case_id": "empty", "characteristics": [], "usage": {}}]
    [point] = trend.points([record])
    assert point.rate == 0.0


def test_a_version_change_is_attributed_to_the_run_it_appears_in():
    series = trend.points([
        _record(0, prompt="p-1"),
        _record(1, prompt="p-1"),
        _record(2, prompt="p-2", passed=1, failed=1),
    ])
    changes = trend.version_changes(series)
    assert "run-1" not in changes, "nothing moved between the first two runs"
    assert "prompt: p-1 → p-2" in changes["run-2"]


def test_every_kind_of_version_change_is_named_separately():
    series = trend.points([
        _record(0, model="a", prompt="p-1", code="1.0.0"),
        _record(1, model="b", prompt="p-2", code="1.0.1"),
    ])
    moved = trend.version_changes(series)["run-1"]
    assert any(m.startswith("model:") for m in moved)
    assert any(m.startswith("prompt:") for m in moved)
    assert any(m.startswith("code:") for m in moved)


def test_a_regression_with_no_version_change_is_left_unattributed():
    """The more interesting case, and it must not be papered over.

    A drop with nothing marked beside it means something moved that the record
    does not carry — an upstream model change, a fixture edit, real variance.
    Inventing an attribution would be worse than showing none.
    """
    series = trend.points([_record(0), _record(1, passed=1, failed=1)])
    assert trend.version_changes(series) == {}


def test_the_page_says_when_it_is_not_showing_everything():
    """No silent caps. A view showing the last N looks identical to one showing
    all of them, and the difference matters exactly when chasing a regression."""
    records = [_record(i) for i in range(10)]
    page = trend.render(records, limit=3)
    assert "Showing the 3 most recent of 10 runs" in page
    assert "7 older run(s) not displayed" in page

    full = trend.render(records, limit=40)
    assert "not displayed" not in full


def test_runs_are_grouped_by_subject_and_ordered_oldest_first():
    a, b = _record(1), _record(0)
    b["subject_version"]["subject"] = "other"
    grouped = trend.by_subject(trend.points([a, b]))
    assert sorted(grouped) == ["demo", "other"]
    series = trend.points([_record(2), _record(0), _record(1)])
    assert [p.run_id for p in series] == ["run-0", "run-1", "run-2"]


def test_the_page_is_self_contained_and_escapes_what_it_renders():
    record = _record(0)
    record["subject_version"]["subject"] = "<script>alert(1)</script>"
    page = trend.render([record])
    assert "<script>alert(1)</script>" not in page, "subject names are data, not markup"
    assert "&lt;script&gt;" in page
    assert "http://" not in page and "https://" not in page, "no external assets"


def test_an_empty_log_renders_a_page_rather_than_crashing():
    page = trend.render([])
    assert "No run records found" in page


def test_a_record_missing_optional_fields_still_renders():
    """The log outlives schema changes — that is why it is kept as raw dicts."""
    minimal = {"run_id": "x", "subject_version": {"subject": "s"}, "results": []}
    [point] = trend.points([minimal])
    assert point.model is None and point.prompt_version is None
    assert "s" in trend.render([minimal])
