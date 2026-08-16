"""Shared runner plumbing for `python -m evals` (RC1-262).

Four consumer repos ship a runner, and roughly a third of each was the same
code — 32 byte-identical lines when this was measured at filing, 46 by the
time RC1-263 had added an identical store selector to every one of them. This
module is the consolidation: **a helper each runner calls, not a framework
each runner plugs into**. Which subject runs, its cases, its summary lines
and its subcommands stay in the repo; what moves here is only what was
already identical.

What deliberately stays out:

* **Credential resolution.** Every repo spells its key differently
  (`LPA_ANTHROPIC_API_KEY`, `settings.anthropic_api_key`, plain
  `ANTHROPIC_API_KEY`), and a library that reached for one would work in
  exactly one repo (ADR-0035). The store variables are the deliberate
  exception: `EVAL_DATABASE_URL` and `EVAL_RUNS_PATH` are identical by
  design in every consumer (RC1-263), which is exactly what makes
  `store_from_env` movable.
* **Aggregation.** `pr_agent` prints recall and clean-diff noise as separate
  lines because merging them would let a flag-everything reviewer score
  perfect recall. Nothing here combines results into one number.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from agent_evals.record import (
    CaseResult,
    RunRecord,
    RunStore,
    SubjectVersion,
    new_run_id,
)
from agent_evals.sql_store import SqlRunStore


class UnknownCase(Exception):
    """`--case` named an id that is not in the suite."""


def select_cases(cases: Iterable, case_id: str | None) -> tuple:
    """The subset a `--case` flag names, or everything when it is None.

    Raises `UnknownCase` rather than returning an empty tuple: a mistyped id
    that silently ran zero cases would exit 0 and read as a pass.
    """
    cases = tuple(cases)
    if case_id is None:
        return cases
    selected = tuple(c for c in cases if c.id == case_id)
    if not selected:
        raise UnknownCase(f"no case {case_id!r}")
    return selected


def store_from_env(default_path: str | Path | None = None) -> RunStore | SqlRunStore:
    """The shared Postgres store when `EVAL_DATABASE_URL` is set, else the
    local JSONL default (RC1-263).

    Both variables are read from the process environment, never a repo `.env`
    — the credential lives in one place outside every repo. An unreachable
    store fails the run loudly: a silent fallback to the file would fork the
    record history.
    """
    dsn = os.environ.get("EVAL_DATABASE_URL")
    if dsn:
        store = SqlRunStore(dsn)
        store.ensure_schema()
        return store
    path = default_path or os.environ.get("EVAL_RUNS_PATH", "./eval-runs/runs.jsonl")
    return RunStore(Path(path))


def print_result(result: CaseResult, *, extra: str = "") -> None:
    """One case's outcome: `pass`/`FAIL`/`ERROR`, then every failing or
    advisory characteristic. Passing non-advisory characteristics stay
    silent — the shape of a failure is the actionable part.

    `extra` extends the header line — `pr_agent` shows finding counts there;
    nothing else needs it.
    """
    if result.error:
        print(f"  ERROR {result.case_id}: {result.error}")
        return
    status = "pass" if result.passed else "FAIL"
    header = f"{result.usage.latency_ms / 1000:.0f}s"
    if extra:
        header += f", {extra}"
    print(f"  {status} {result.case_id}  ({header})")
    for c in result.characteristics:
        if c.passed and not c.advisory:
            continue
        mark = "~" if c.advisory else "✗"
        tag = " [advisory]" if c.advisory else ""
        print(f"    {mark} {c.name}{tag}: {c.detail}")


def record_run(
    version: SubjectVersion,
    started: datetime,
    results: Sequence[CaseResult],
    *,
    store: RunStore | SqlRunStore | None = None,
) -> RunRecord:
    """Build the record, append it, announce the id. Returns the record.

    `finished_at` is stamped here — call this when the run is actually over.
    The store defaults to `store_from_env()`; pass one to override.
    """
    record = RunRecord(
        run_id=new_run_id(version.subject),
        subject_version=version,
        started_at=started,
        finished_at=datetime.now(UTC),
        results=list(results),
    )
    (store if store is not None else store_from_env()).append(record)
    print(f"\nrun {record.run_id} recorded")
    return record


def exit_code(results: Sequence[CaseResult]) -> int:
    """`0` all passed, `1` a case failed, `2` a case errored — CI-shaped and
    identical in every consumer, so a workflow step reads the same in all of
    them. Errors outrank failures: a subject that produced nothing to score
    is a different problem from one that answered badly.
    """
    if any(r.error for r in results):
        return 2
    return 0 if all(r.passed for r in results) else 1
