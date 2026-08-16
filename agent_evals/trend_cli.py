"""`python -m agent_evals.trend_cli` — build the trend page (RC1-255).

Reads run records from local directories or from the consumer repos on GitHub,
renders one self-contained HTML page, and writes it out. No credentials: every
consumer repo is public, so the records are fetched over plain HTTPS.

Fetching is deliberately read-only and one-directional. RC1-248 and RC1-255 both
rejected cross-repo *dispatch* — machinery that needs tokens, write access and
coordination between workflows. Reading five public files needs none of that,
and the difference is the reason this is a workflow step rather than a service.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

from agent_evals import trend

#: The repos that commit run records, and where the records live in each.
SOURCES: tuple[tuple[str, str], ...] = (
    ("launch-planner-agent", "eval-runs/runs.jsonl"),
    ("tpm-automation-platform", "eval-runs/runs.jsonl"),
    ("pr_agent", "eval-runs/runs.jsonl"),
    ("n8n-stakeholder-status-email", "eval-runs/runs.jsonl"),
    ("n8n-concert-intelligence", "eval-runs/runs.jsonl"),
)

_RAW = "https://raw.githubusercontent.com/{owner}/{repo}/main/{path}"


def fetch(owner: str) -> tuple[list[dict], list[str]]:
    """Records from every source repo, plus a note per source that had none.

    A repo with no records yet is normal and not an error — it means nobody has
    run its billed suite since the log was committed. The notes are surfaced on
    the page so "this subject is missing" is never mistaken for "this subject
    has no runs".
    """
    import json

    records: list[dict] = []
    notes: list[str] = []
    for repo, path in SOURCES:
        url = _RAW.format(owner=owner, repo=repo, path=path)
        try:
            with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
                body = response.read().decode()
        except urllib.error.HTTPError as exc:
            notes.append(f"{repo}: no records ({exc.code})")
            continue
        except OSError as exc:
            notes.append(f"{repo}: unreachable ({exc})")
            continue
        count = 0
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
                count += 1
            except json.JSONDecodeError:
                notes.append(f"{repo}: skipped a malformed line")
        if not count:
            notes.append(f"{repo}: log is present but empty")
    return records, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-evals trend", description=__doc__)
    parser.add_argument("--owner", default="snacksnack", help="GitHub owner of the source repos")
    parser.add_argument("--local", type=Path, help="read from a local directory instead")
    parser.add_argument("--out", type=Path, default=Path("site/index.html"))
    parser.add_argument("--limit", type=int, default=trend.DEFAULT_LIMIT)
    args = parser.parse_args(argv)

    if args.local:
        records, notes = trend.load_dir(args.local), []
    else:
        records, notes = fetch(args.owner)

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
