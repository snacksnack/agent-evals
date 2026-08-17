"""Quality per subject over time, attributed to a model or a prompt (RC1-255).

A pure function: run records in, HTML out. No service, no database, no
credentials — because the records are already an append-only log carrying the
model, the prompt version and the code version, and git already stores an
append-only log with history and blame. Standing up a second one would be
duplicating what the repo does for free.

## What the view is actually for

One question: **a score moved — what moved with it?** So every subject is drawn
as a time series and every point carries its three versions. Where a version
changes between consecutive runs, the row is marked. A drop next to a mark is a
regression with a suspect; a drop with no mark is a regression in something
nobody edited, which is a different and more interesting problem.

That is the whole claim. It does not do significance testing, and with runs
numbering in the dozens it should not pretend to.

## Only deliberate runs belong here

Billed suites are run by hand at decision points, so each record is a
measurement someone chose to take. Free CI runs are a *gate*: they answer
pass/fail on every push, and trending "the deterministic checker still returns
36/36" would bury the signal under noise. The renderer does not enforce this —
it renders what it is given — but `docs/trend.md` records why the consumer
repos commit only the billed runs.

## No silent caps

`render` never truncates without saying so. If a subject has more runs than the
display limit, the page says how many were dropped and which end. A trend view
that quietly shows the last N looks identical to one showing everything, and
the difference matters exactly when someone is chasing a regression.
"""

from __future__ import annotations

import html
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

#: Runs shown per subject. Anything older is summarised rather than dropped
#: silently — see the module docstring.
DEFAULT_LIMIT = 40


@dataclass(frozen=True)
class Point:
    """One run of one subject, flattened for display."""

    run_id: str
    started_at: datetime
    subject: str
    model: str | None
    prompt_version: str | None
    code_version: str
    passed: int
    failed: int
    errored: int
    advisory_failed: int
    cost_usd: Decimal
    latency_ms: float

    @property
    def total(self) -> int:
        return self.passed + self.failed

    @property
    def rate(self) -> float:
        """Share of cases whose gating characteristics all passed."""
        return self.passed / self.total if self.total else 0.0

    def versions(self) -> tuple[str | None, str | None, str]:
        return (self.model, self.prompt_version, self.code_version)


def points(records: Iterable[dict]) -> list[Point]:
    """Flatten raw run records into points, oldest first.

    Takes dicts rather than `RunRecord` so a consumer can render records written
    by an older version of this library without a migration — the trend view
    outliving a schema change is the entire point of keeping the log.
    """
    out = []
    for record in records:
        version = record.get("subject_version") or {}
        results = record.get("results") or []
        passed = sum(1 for r in results if _passed(r))
        errored = sum(1 for r in results if r.get("error"))
        advisory = sum(
            1
            for r in results
            for c in (r.get("characteristics") or [])
            if c.get("advisory") and not c.get("passed")
        )
        out.append(
            Point(
                run_id=record.get("run_id", ""),
                started_at=_parse(record.get("started_at")),
                subject=version.get("subject", "unknown"),
                model=version.get("model"),
                prompt_version=version.get("prompt_version"),
                code_version=version.get("code_version", ""),
                passed=passed,
                failed=len(results) - passed,
                errored=errored,
                advisory_failed=advisory,
                cost_usd=sum(
                    (Decimal(str((r.get("usage") or {}).get("cost_usd", 0))) for r in results),
                    Decimal(0),
                ),
                latency_ms=sum((r.get("usage") or {}).get("latency_ms", 0.0) for r in results),
            )
        )
    return sorted(out, key=lambda p: p.started_at)


def _passed(result: dict) -> bool:
    """A case passes when every *non-advisory* characteristic passes.

    Recomputed here rather than trusted from a stored flag: advisory results
    can never fail a build (ADR-0033/0034), and a renderer that folded them in
    would report a subject as regressing on a dimension explicitly declared
    unable to gate.
    """
    if result.get("error"):
        return False
    characteristics = result.get("characteristics") or []
    if not characteristics:
        return False
    return all(c.get("passed") for c in characteristics if not c.get("advisory"))


def _parse(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return datetime.min


def by_subject(all_points: Sequence[Point]) -> dict[str, list[Point]]:
    grouped: dict[str, list[Point]] = {}
    for point in all_points:
        grouped.setdefault(point.subject, []).append(point)
    return dict(sorted(grouped.items()))


def version_changes(series: Sequence[Point]) -> dict[str, list[str]]:
    """What changed between each run and the one before it.

    Keyed by `run_id`. This is the attribution the whole view exists for: a
    score that moves next to a version change has a suspect, and one that moves
    without a version change means something outside the record moved.
    """
    changes: dict[str, list[str]] = {}
    for previous, current in zip(series, series[1:], strict=False):
        moved = []
        for label, before, after in (
            ("model", previous.model, current.model),
            ("prompt", previous.prompt_version, current.prompt_version),
            ("code", previous.code_version, current.code_version),
        ):
            if before != after:
                moved.append(f"{label}: {_short(before)} → {_short(after)}")
        if moved:
            changes[current.run_id] = moved
    return changes


def _short(value: str | None) -> str:
    if not value:
        return "none"
    return value if len(value) <= 28 else value[:25] + "…"


# --- rendering ------------------------------------------------------------

_CSS = """
:root { --bg:#fff; --fg:#1a1a1a; --muted:#666; --line:#e4e4e7;
        --ok:#15803d; --bad:#b91c1c; --warn:#a16207; --mark:#f5f3ff; --vc:#7c3aed; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#111; --fg:#ededed; --muted:#9a9a9a; --line:#2a2a2a;
          --ok:#4ade80; --bad:#f87171; --warn:#fbbf24; --mark:#1e1b31; --vc:#8b5cf6; }
}
body { background:var(--bg); color:var(--fg); margin:0 auto; padding:2rem 1.25rem;
       max-width:60rem; font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
h1 { font-size:1.5rem; margin:0 0 .25rem; }
h2 { font-size:1.05rem; margin:2.5rem 0 .5rem; padding-bottom:.3rem;
     border-bottom:1px solid var(--line); }
.sub { color:var(--muted); margin:0 0 2rem; }
.wrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:13px; }
th { text-align:left; font-weight:600; color:var(--muted); padding:.4rem .6rem;
     border-bottom:1px solid var(--line); white-space:nowrap; }
td { padding:.4rem .6rem; border-bottom:1px solid var(--line); white-space:nowrap; }
tr.changed td { background:var(--mark); }
.ok { color:var(--ok); } .bad { color:var(--bad); } .warn { color:var(--warn); }
.muted { color:var(--muted); }
.bar { display:inline-block; height:.55rem; border-radius:2px; background:var(--ok);
       vertical-align:middle; min-width:1px; }
.bar.bad { background:var(--bad); }
.note { color:var(--muted); font-size:12px; margin:.4rem 0 0; }
svg.spark { display:block; margin:.2rem 0 .4rem; max-width:100%; }
.spark .grid { stroke:var(--line); stroke-width:1; }
.spark .axis { fill:var(--muted); font:10px ui-monospace,SFMono-Regular,Menlo,monospace; }
.spark .line { fill:none; stroke:var(--fg); stroke-width:1.5; }
.spark .pt { fill:var(--muted); }
.spark .pt.vc { fill:var(--vc); }
.spark .hit { fill:transparent; }
code { font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace; }
"""


def render(records: Iterable[dict], *, limit: int = DEFAULT_LIMIT, generated: str = "") -> str:
    """The trend page, as one self-contained HTML document."""
    all_points = points(records)
    grouped = by_subject(all_points)
    body = [
        "<h1>Agent eval trend</h1>",
        f'<p class="sub">Quality per subject over time, annotated with the model and '
        f"prompt version each run used. {len(all_points)} run(s) across "
        f"{len(grouped)} subject(s).{(' Generated ' + html.escape(generated)) if generated else ''}"
        "</p>",
    ]
    if not grouped:
        body.append('<p class="note">No run records found.</p>')
    for subject, series in grouped.items():
        body.append(_section(subject, series, limit))
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Agent eval trend</title><style>{_CSS}</style></head><body>"
        + "\n".join(body)
        + "</body></html>"
    )


def _section(subject: str, series: Sequence[Point], limit: int) -> str:
    changes = version_changes(series)
    shown = list(reversed(series))[:limit]
    dropped = len(series) - len(shown)
    spark = _sparkline(subject, series[len(series) - len(shown) :], changes)

    rows = []
    for point in shown:
        moved = changes.get(point.run_id, [])
        cells = [
            f"<td><code>{html.escape(point.started_at.strftime('%Y-%m-%d %H:%M'))}</code></td>",
            f"<td>{_rate_cell(point)}</td>",
            f"<td>{_counts_cell(point)}</td>",
            f"<td><code class='muted'>{html.escape(_short(point.model))}</code></td>",
            f"<td><code class='muted'>{html.escape(_short(point.prompt_version))}</code></td>",
            f"<td>{_cost_cell(point)}</td>",
            f"<td class='muted'>{html.escape('; '.join(moved)) if moved else ''}</td>",
        ]
        rows.append(f"<tr class='{'changed' if moved else ''}'>{''.join(cells)}</tr>")

    note = ""
    if dropped:
        note = (
            f'<p class="note">Showing the {len(shown)} most recent of {len(series)} runs; '
            f"{dropped} older run(s) not displayed.</p>"
        )
    return (
        f"<h2>{html.escape(subject)}</h2>{spark}<div class='wrap'><table>"
        "<tr><th>run</th><th>pass rate</th><th>cases</th><th>model</th>"
        "<th>prompt</th><th>cost</th><th>changed since previous</th></tr>"
        + "".join(rows)
        + f"</table></div>{note}"
    )


#: Sparkline geometry in viewBox units: overall size, then the plot rectangle
#: (left, top, right, bottom). The left gutter holds the y-axis labels.
_SPARK_W, _SPARK_H = 260, 64
_PLOT = (36, 8, 254, 56)

#: Below this many runs a line chart is a shape with no trend in it — two
#: points always draw a clean slope. Draw nothing rather than something
#: misleading.
_MIN_SPARK_RUNS = 3

#: Minimum y-axis span, in pass-rate terms. The axis hugs the data so a small
#: dip stays legible at this size, but a hugged axis can exaggerate — the
#: printed min/max labels are what keep that honest, and a floor on the span
#: keeps a near-flat series from turning noise into cliffs.
_MIN_SPAN = 0.05


def _sparkline(subject: str, series: Sequence[Point], changes: dict[str, list[str]]) -> str:
    """Pass rate over the table's window, oldest → newest, as inline SVG.

    Runs where a version changed get a larger accented dot; every dot carries
    an SVG `<title>` (date, rate, what moved), which is a native hover tooltip
    with no JavaScript — the page stays a single self-contained file.
    """
    if len(series) < _MIN_SPARK_RUNS:
        return ""
    left, top, right, bottom = _PLOT
    lo, hi = min(p.rate for p in series), max(p.rate for p in series)
    if hi - lo < _MIN_SPAN:
        lo = max(0.0, lo - (_MIN_SPAN - (hi - lo)) / 2)
        hi = min(1.0, lo + _MIN_SPAN)
        lo = max(0.0, hi - _MIN_SPAN)

    def x(i: int) -> float:
        return round(left + i * (right - left) / (len(series) - 1), 1)

    def y(rate: float) -> float:
        return round(bottom - (rate - lo) / (hi - lo) * (bottom - top), 1)

    dots = []
    for i, point in enumerate(series):
        moved = changes.get(point.run_id, [])
        tip = (
            f"{point.started_at.strftime('%Y-%m-%d %H:%M')} — "
            f"{_axis_pct(point.rate)} ({point.passed}/{point.total})"
        )
        if moved:
            tip += " — " + "; ".join(moved)
        cls, radius = ("pt vc", 3.5) if moved else ("pt", 2)
        dots.append(
            f"<g><title>{html.escape(tip)}</title>"
            f"<circle class='hit' cx='{x(i)}' cy='{y(point.rate)}' r='8'/>"
            f"<circle class='{cls}' cx='{x(i)}' cy='{y(point.rate)}' r='{radius}'/></g>"
        )

    path = " ".join(f"{x(i)},{y(p.rate)}" for i, p in enumerate(series))
    return (
        f"<svg class='spark' role='img' width='{_SPARK_W}' height='{_SPARK_H}' "
        f"viewBox='0 0 {_SPARK_W} {_SPARK_H}' "
        f"aria-label='Pass rate per run for {html.escape(subject)}, oldest to newest'>"
        f"<line class='grid' x1='{left}' y1='{top}' x2='{right}' y2='{top}'/>"
        f"<line class='grid' x1='{left}' y1='{bottom}' x2='{right}' y2='{bottom}'/>"
        f"<text class='axis' x='{left - 5}' y='{top + 3}' "
        f"text-anchor='end'>{_axis_pct(hi)}</text>"
        f"<text class='axis' x='{left - 5}' y='{bottom + 3}' "
        f"text-anchor='end'>{_axis_pct(lo)}</text>"
        f"<polyline class='line' points='{path}'/>" + "".join(dots) + "</svg>"
    )


def _axis_pct(rate: float) -> str:
    return f"{rate * 100:.1f}".rstrip("0").rstrip(".") + "%"


def _rate_cell(point: Point) -> str:
    pct = point.rate * 100
    width = max(1, round(point.rate * 60))
    cls = "ok" if point.rate == 1 else "bad"
    return (
        f"<span class='bar {'' if point.rate == 1 else 'bad'}' style='width:{width}px'></span> "
        f"<span class='{cls}'>{pct:.0f}%</span>"
    )


def _counts_cell(point: Point) -> str:
    parts = [f"{point.passed}/{point.total}"]
    if point.errored:
        parts.append(f"<span class='bad'>{point.errored} errored</span>")
    if point.advisory_failed:
        parts.append(f"<span class='warn'>{point.advisory_failed} advisory</span>")
    return " · ".join(parts)


def _cost_cell(point: Point) -> str:
    if point.cost_usd == 0:
        return "<span class='muted'>free</span>"
    return f"${point.cost_usd:.4f}"


def load_dir(root: Path) -> list[dict]:
    """Every `*.jsonl` under `root`, as raw records.

    A malformed line is skipped rather than fatal: the log is append-only and
    written by five repos, so one bad line must not take the whole view down.
    Skipped lines are counted into the returned records' absence rather than
    reported — `render` shows what it was given, and the count of runs on the
    page is the honest signal that something is missing.
    """
    records = []
    for path in sorted(root.rglob("*.jsonl")):
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records
