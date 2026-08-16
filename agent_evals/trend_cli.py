"""`python -m agent_evals.trend_cli` — build the trend page (RC1-255, RC1-263).

Reads run records from the eval store (`EVAL_DATABASE_URL`) or from a local
directory, renders one self-contained HTML page, and writes it out.

The page is rendered and published from a developer machine, never CI.
Records only exist because a human ran a suite, so the machine that just
wrote them is the machine that rebuilds the page — and CI holds no database
credential (RC1-263). `scripts/publish_trend.sh` does both steps.

An unreachable store is a hard failure, not a note: silently rendering fewer
records than exist would understate a trend, the same rule `RunStore.all`
applies to a malformed line. Only an empty store is a note, because "no runs
yet" is a true statement the page should make.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from agent_evals import trend


def fetch(dsn: str) -> tuple[list[dict], list[str]]:
    """Every record in the store, oldest first, plus notes for the page."""
    from agent_evals.sql_store import SqlRunStore

    store = SqlRunStore(dsn)
    try:
        records = store.raw()
    finally:
        store.close()
    notes = [] if records else ["the store has no records yet"]
    return records, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-evals trend", description=__doc__)
    parser.add_argument("--local", type=Path, help="read from a local directory instead")
    parser.add_argument("--out", type=Path, default=Path("site/index.html"))
    parser.add_argument("--limit", type=int, default=trend.DEFAULT_LIMIT)
    args = parser.parse_args(argv)

    if args.local:
        records, notes = trend.load_dir(args.local), []
    else:
        dsn = os.environ.get("EVAL_DATABASE_URL")
        if not dsn:
            parser.error(
                "EVAL_DATABASE_URL is not set and --local was not given — "
                "the store's location lives in one place outside the repos (RC1-263)"
            )
        records, notes = fetch(dsn)

    for note in notes:
        print(f"  note: {note}", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    page = trend.render(records, limit=args.limit, generated=_stamp())
    if notes:
        page = page.replace(
            "</body>",
            '<p class="note">Sources with nothing to show: '
            + "; ".join(n.replace("<", "&lt;") for n in notes)
            + "</p></body>",
        )
    args.out.write_text(page)
    print(f"{len(records)} record(s) -> {args.out}")
    return 0


def _stamp() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


if __name__ == "__main__":
    raise SystemExit(main())
