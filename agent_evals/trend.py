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

## The charts do not exaggerate (RC1-270)

Every per-subject y-axis pins its top at 100% — a flat perfect series sits on
the ceiling, where "perfect" belongs, instead of floating mid-chart looking
like volatility. The bottom hugs the data in 0.05 steps and both bounds are
printed. The all-subjects overview is fixed 0–100%. Runs with errored cases
are drawn differently from runs with a low pass rate, because an eval that
crashed and an eval that failed are different findings.
"""

from __future__ import annotations

import html
import json
import math
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

#: One deliberate look. Every colour is pinned in the ticket (RC1-270) with a
#: floor: no *text* resolves lighter than oklch(0.52 0.01 60) against its
#: background — strokes and gridlines may go lighter, glyphs may not.
_CSS = """
:root { color-scheme: light;
  --paper:oklch(0.985 0.004 85); --panel:oklch(1 0 0);
  --ink:oklch(0.28 0.01 60); --muted:oklch(0.52 0.01 60);
  --rule:oklch(0.9 0.006 75); --rule-dark:oklch(0.75 0.01 75);
  --ok:oklch(0.46 0.12 150); --bad:oklch(0.53 0.17 25);
  --warn:oklch(0.5 0.12 70); --vc:oklch(0.55 0.16 295);
  --steady:oklch(0.8 0.008 75);
  --ok-tint:oklch(0.95 0.03 150); --bad-tint:oklch(0.95 0.03 25);
  --vc-tint:oklch(0.96 0.02 295);
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
body { background:var(--paper); color:var(--ink); margin:0 auto;
       padding:1.5rem 1rem 3rem; max-width:64rem; font:14px/1.55 var(--sans); }
.card { background:var(--panel); border:1px solid var(--rule); border-radius:8px;
        padding:1.25rem 1.5rem; margin:0 0 1.25rem; }
h1 { font-size:1.35rem; margin:0 0 .3rem; }
h2 { font-size:1rem; margin:0; }
.masthead { display:flex; justify-content:space-between; gap:2rem; flex-wrap:wrap; }
.tagline { color:var(--muted); margin:.1rem 0 1rem; max-width:26rem; }
.stats { display:flex; gap:1.6rem; margin:0; }
.stats div { margin:0; }
.stats dt { font:11px var(--mono); color:var(--muted); }
.stats dd { font:13px var(--mono); margin:0; }
.overall { text-align:right; min-width:14rem; }
.overall .k { font:11px var(--mono); letter-spacing:.08em; text-transform:uppercase;
              color:var(--muted); }
.overall .big { font:700 2rem/1.2 var(--mono); }
.overall .cases { font:12px var(--mono); color:var(--muted); }
.meter { height:6px; border-radius:3px; background:var(--rule); margin:.4rem 0;
         overflow:hidden; }
.meter > div { height:100%; background:var(--ok); }
.shead { display:flex; align-items:baseline; gap:.6rem; flex-wrap:wrap;
         margin:2rem 0 .4rem; padding-top:1.5rem; border-top:1px solid var(--rule); }
section.subject:first-of-type .shead { margin-top:0; padding-top:0; border-top:0; }
.shead h2 { font-family:var(--mono); }
.shead .grow { flex:1; }
.pill { font:11px var(--mono); color:var(--muted); border:1px solid var(--rule);
        border-radius:4px; padding:.05rem .4rem; }
.runsn { font:12px var(--mono); color:var(--muted); }
.now { font:700 15px var(--mono); }
.chip { font:11px var(--mono); border-radius:4px; padding:.1rem .35rem; }
.chip.up { color:var(--ok); background:var(--ok-tint); }
.chip.down { color:var(--bad); background:var(--bad-tint); }
.chip.flat { color:var(--muted); border:1px solid var(--rule); }
.wrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font:12.5px var(--mono); }
th { text-align:left; font-weight:500; color:var(--muted); padding:.35rem .6rem;
     border-bottom:1px solid var(--rule); white-space:nowrap; }
td { padding:.35rem .6rem; border-bottom:1px solid var(--rule); white-space:nowrap; }
tr:last-child td { border-bottom:0; }
tr.changed td { background:var(--vc-tint); }
.ok { color:var(--ok); } .bad { color:var(--bad); } .warn { color:var(--warn); }
.muted { color:var(--muted); }
.bar { display:inline-block; height:.5rem; border-radius:2px; background:var(--ok);
       vertical-align:middle; min-width:1px; }
.bar.warn { background:var(--warn); }
.note { color:var(--muted); font-size:12px; margin:.4rem 0 0; }
.desc { color:var(--muted); font-size:12.5px; margin:-.15rem 0 .5rem; max-width:52rem; }
svg { display:block; max-width:100%; height:auto; }
svg.spark { margin:.2rem 0 .6rem; }
svg.ov { margin:.8rem 0 .2rem; }
.grid { stroke:var(--rule); stroke-width:1; }
.grid.mid { stroke-dasharray:3 3; }
.grid.dark { stroke:var(--rule-dark); }
.axis { fill:var(--muted); font:10px var(--mono); }
.tline { fill:none; stroke-width:2; }
.ov .tline { stroke-width:2.1; }
.ov .tline.steady { stroke:var(--steady); stroke-width:1.4; }
.pt.err { fill:var(--panel); stroke:var(--bad); stroke-width:1.8; }
.pt.vc { fill:var(--vc); }
.hit { fill:transparent; }
.leader { stroke:var(--rule-dark); stroke-width:1; stroke-dasharray:2 3; }
.slabel .name { font:11px var(--mono); }
.slabel .val { font:10px var(--mono); fill:var(--muted); }
.ovhead { display:flex; justify-content:space-between; align-items:center;
          gap:1rem; flex-wrap:wrap; }
.controls { display:flex; gap:.5rem; }
.seg { display:inline-flex; border:1px solid var(--rule); border-radius:6px;
       overflow:hidden; }
.seg button { font:11px var(--mono); background:var(--panel); color:var(--muted);
              border:0; padding:.3rem .6rem; cursor:pointer; }
.seg button[aria-pressed='true'] { background:var(--ink); color:var(--panel); }
.readout { font:12px var(--mono); color:var(--muted); background:var(--paper);
           border:1px solid var(--rule); border-radius:6px; padding:.5rem .8rem;
           margin:.8rem 0 0; }
.chips { display:flex; flex-wrap:wrap; gap:.4rem; margin-top:.6rem; }
.chipbtn { display:inline-flex; align-items:center; gap:.35rem; font:11px var(--mono);
           color:var(--muted); background:var(--panel); border:1px solid var(--rule);
           border-radius:999px; padding:.25rem .6rem; }
.chipbtn[aria-pressed='true'] { border-color:var(--ink); color:var(--ink); }
body.js .chipbtn, body.js .ov .series, body.js .ov .slabel { cursor:pointer; }
.ov.movers-only .series:not(.mover) { display:none; }
.ov.iso .series:not(.sel), .ov.iso .slabel:not(.sel),
.ov.iso .leader:not(.sel) { opacity:.12; }
.legend { display:flex; gap:1.4rem; flex-wrap:wrap; align-items:center;
          margin-top:2rem; padding-top:1rem; border-top:1px solid var(--rule);
          font:11px var(--mono); color:var(--muted); }
.legend .attrib { margin-left:auto; }
.dot { display:inline-block; width:8px; height:8px; border-radius:50%;
       vertical-align:-1px; margin-right:.3rem; }
.dot.run { background:var(--ink); }
.dot.err { background:var(--panel); border:2px solid var(--bad); }
.dot.vc { background:var(--vc); }
code { font:12px/1.4 var(--mono); }
"""

#: The page's only JavaScript, inlined — one file, no external request, no
#: credential (the constraint RC1-271 restated; docs/trend.md has the
#: reasoning). Everything here asks questions of the chart the server already
#: drew: isolate a subject, drop the flat series, lay x back out by real time,
#: read a point without waiting for a native tooltip. With scripting disabled
#: none of it runs and nothing on the page disappears — the controls and
#: readout ship `hidden` and are only revealed from here.
_JS = """
(function () {
  'use strict';
  var ov = document.querySelector('svg.ov');
  if (!ov) return;
  document.body.classList.add('js');
  document.querySelectorAll('.jsonly').forEach(function (el) { el.hidden = false; });
  var readout = document.getElementById('readout');
  var rest = readout ? readout.textContent : '';
  var iso = null, movers = false, byrun = true;
  function pressed(btn, on) { if (btn) btn.setAttribute('aria-pressed', String(on)); }
  function status() {
    return iso ? 'isolated: ' + iso + ' — click again to release' : rest;
  }
  function apply() {
    ov.classList.toggle('movers-only', movers);
    ov.classList.toggle('iso', iso !== null);
    ov.querySelectorAll('.series, .slabel, .leader').forEach(function (el) {
      el.classList.toggle('sel', el.getAttribute('data-subject') === iso);
    });
    document.querySelectorAll('.chipbtn').forEach(function (b) {
      pressed(b, b.getAttribute('data-subject') === iso);
    });
    ov.querySelectorAll('polyline[data-rpts]').forEach(function (p) {
      p.setAttribute('points', p.getAttribute(byrun ? 'data-rpts' : 'data-tpts'));
    });
    ov.querySelectorAll('circle[data-rx]').forEach(function (c) {
      c.setAttribute('cx', c.getAttribute(byrun ? 'data-rx' : 'data-tx'));
    });
    ov.querySelectorAll('line[data-rx1]').forEach(function (l) {
      l.setAttribute('x1', l.getAttribute(byrun ? 'data-rx1' : 'data-tx1'));
    });
    var tt = ov.querySelector('.ticks-time'), tr = ov.querySelector('.ticks-run');
    if (tt) tt.style.display = byrun ? 'none' : '';
    if (tr) tr.style.display = byrun ? '' : 'none';
    if (readout) readout.textContent = status();
  }
  function toggleIso(name) { iso = iso === name ? null : name; apply(); }
  ov.addEventListener('click', function (e) {
    var g = e.target.closest('[data-subject]');
    if (g) toggleIso(g.getAttribute('data-subject'));
  });
  document.querySelectorAll('.chipbtn').forEach(function (b) {
    b.addEventListener('click', function () { toggleIso(b.getAttribute('data-subject')); });
  });
  ov.addEventListener('mousemove', function (e) {
    if (!readout) return;
    var g = e.target.closest('g');
    var first = g && g.firstElementChild;
    if (first && first.nodeName.toLowerCase() === 'title') {
      readout.textContent = first.textContent;
      return;
    }
    var tipped = e.target.closest('[data-tip]');
    readout.textContent = tipped ? tipped.getAttribute('data-tip') : status();
  });
  ov.addEventListener('mouseleave', function () {
    if (readout) readout.textContent = status();
  });
  function seg(onId, offId, fn) {
    var on = document.getElementById(onId), off = document.getElementById(offId);
    if (!on) return;
    on.addEventListener('click', function () {
      fn(); pressed(on, true); pressed(off, false); apply();
    });
  }
  seg('b-all', 'b-movers', function () { movers = false; });
  seg('b-movers', 'b-all', function () { movers = true; });
  seg('b-time', 'b-run', function () { byrun = false; });
  seg('b-run', 'b-time', function () { byrun = true; });
})();
"""

#: Categorical series palette — six hues at pinned lightness/chroma, assigned
#: to movers in subject order and cycled if there are ever more than six.
_SERIES = (
    "oklch(0.55 0.15 255)",
    "oklch(0.55 0.15 315)",
    "oklch(0.6 0.14 40)",
    "oklch(0.5 0.12 165)",
    "oklch(0.52 0.13 205)",
    "oklch(0.5 0.15 350)",
)


def render(
    records: Iterable[dict],
    *,
    limit: int = DEFAULT_LIMIT,
    generated: str = "",
    descriptions: dict[str, str] | None = None,
    now: datetime | None = None,
) -> str:
    """The trend page, as one self-contained HTML document.

    `descriptions` maps a subject to one sentence saying what it measures
    (RC1-272). The copy is presentation, so it arrives as an argument rather
    than travelling in the records — the store schema and the consumer repos
    know nothing about it. A subject without an entry renders no line: new
    subjects appear on the page before anyone writes copy for them.

    `now` anchors the per-subject freshness labels ("last run N days ago",
    RC1-314). It arrives as an argument rather than being read from the clock
    so the function stays pure; without it no label renders.
    """
    all_points = points(records)
    grouped = by_subject(all_points)
    windows = {s: series[-limit:] if limit else list(series) for s, series in grouped.items()}
    colours = _colours(windows)
    descriptions = descriptions or {}

    body = [_masthead(windows, all_points, generated)]
    if not grouped:
        body.append('<p class="note">No run records found.</p>')
    else:
        body.append(_overview_card(windows, colours, descriptions))
        sections = [
            _section(subject, grouped[subject], windows[subject], colours, descriptions, now)
            for subject in grouped
        ]
        body.append(
            "<div class='card'>" + "".join(sections) + _legend(windows) + "</div>"
        )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Agent eval trend</title><style>{_CSS}</style></head><body>"
        + "\n".join(body)
        + f"<script>{_JS}</script></body></html>"
    )


def _overview_card(
    windows: dict[str, list[Point]], colours: dict[str, str], descriptions: dict[str, str]
) -> str:
    """The overview chart with its interactions' chrome.

    The controls and readout ship `hidden` and the inline script reveals
    them: with JavaScript disabled the chart, every label and every value
    still render — the interactions degrade, nothing disappears, and no dead
    control is shown (RC1-271). The chips stay visible either way, because
    they carry the steady subjects' rates, which the chart does not label.
    """
    controls = (
        "<div class='controls jsonly' hidden>"
        "<div class='seg'>"
        f"<button id='b-all' aria-pressed='true'>all {len(windows)}</button>"
        "<button id='b-movers' aria-pressed='false'>movers only</button></div>"
        "<div class='seg'>"
        "<button id='b-run' aria-pressed='true'>by run #</button>"
        "<button id='b-time' aria-pressed='false'>by time</button></div></div>"
    )
    chips = []
    for subject, window in windows.items():
        last = window[-1]
        colour = colours.get(subject, "var(--steady)")
        desc = descriptions.get(subject)
        tip = f" title='{html.escape(desc)}'" if desc else ""
        chips.append(
            f"<button class='chipbtn' data-subject='{html.escape(subject)}' "
            f"aria-pressed='false'{tip}>"
            f"<span class='dot' style='background:{colour}'></span>"
            f"{html.escape(subject)} <span>{_axis_pct(last.rate)}</span></button>"
        )
    return (
        "<section class='card'><div class='ovhead'>"
        "<h2>All subjects, one axis</h2>" + controls + "</div>"
        "<div class='readout jsonly' id='readout' hidden>"
        "hover a line — click to isolate a subject</div>"
        + _overview(windows, colours)
        + f"<div class='chips'>{''.join(chips)}</div></section>"
    )


def _movers(windows: dict[str, list[Point]]) -> set[str]:
    """A subject moved if its displayed window is not flat.

    The threshold is deliberately zero: any movement at all earns a colour,
    because the colour is what routes the eye to the series worth reading.
    """
    return {
        s
        for s, w in windows.items()
        if len(w) >= 2 and max(p.rate for p in w) > min(p.rate for p in w)
    }


def _colours(windows: dict[str, list[Point]]) -> dict[str, str]:
    return {s: _SERIES[i % len(_SERIES)] for i, s in enumerate(sorted(_movers(windows)))}


def _masthead(windows: dict[str, list[Point]], all_points: Sequence[Point], generated: str) -> str:
    """Leads with the overall pass rate: latest run of each subject, stated as
    such — not an average of averages."""
    latest = [w[-1] for w in windows.values() if w]
    total = sum(p.total for p in latest)
    passed = sum(p.passed for p in latest)
    overall = passed / total if total else 0.0
    perfect = sum(1 for p in latest if p.total and p.rate == 1)
    spend = sum((p.cost_usd for p in all_points), Decimal(0))

    stats = [
        ("runs", str(len(all_points))),
        ("subjects", str(len(windows))),
        ("billed spend", f"${spend:.2f}"),
    ]
    if generated:
        stats.append(("generated", html.escape(generated)))
    stats_html = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in stats)

    overall_html = ""
    if latest:
        overall_html = (
            "<div class='overall'><div class='k'>overall pass rate</div>"
            f"<span class='big'>{_axis_pct(overall)}</span> "
            f"<span class='cases'>{passed}/{total} cases</span>"
            f"<div class='meter'><div style='width:{overall * 100:.1f}%'></div></div>"
            f"<div class='cases'>latest run of each subject · "
            f"{perfect}/{len(latest)} at 100%</div></div>"
        )
    return (
        "<header class='card masthead'><div><h1>Agent eval trend</h1>"
        "<p class='tagline'>A score moved — what moved with it? "
        "Every point carries its model, prompt and code version.</p>"
        f"<dl class='stats'>{stats_html}</dl></div>"
        + overall_html
        + "</header>"
    )


# --- the all-subjects overview -------------------------------------------

#: Overview geometry in viewBox units: canvas, then the plot rectangle
#: (left, top, right, bottom). 700→900 is the right-edge label gutter.
_OV_W, _OV_H = 900, 400
_OV_PLOT = (52, 20, 700, 310)

#: Minimum vertical gap between right-edge label blocks, in viewBox units.
_LABEL_GAP = 26


def _overview(windows: dict[str, list[Point]], colours: dict[str, str]) -> str:
    """Every subject on one fixed 0–100% axis, x indexed by run number.

    Movers get a categorical colour and a right-edge label; steady subjects
    draw grey and unlabelled underneath. Without that split, fourteen lines
    stacked on 100% are unreadable — the grey lines are the proof that nothing
    is hidden, the coloured ones are the reading.

    Every x-position is emitted twice: laid out by run number (the default and
    the no-JS fallback) and by real time (`data-` attributes the inline script
    swaps in). Neither axis is correct alone — on a run axis three sweeps and
    thirteen runs look like the same amount of history; on a time axis a
    debugging burst compresses into a sliver (RC1-271). Run number won the
    default (RC1-314) because runs are deliberate and sparse: on a time axis
    ten quiet days render as a stretch of nothing that reads as a broken
    chart, when the honest statement — made by the freshness labels — is that
    nobody took a measurement.
    """
    left, top, right, bottom = _OV_PLOT
    everything = [p for w in windows.values() for p in w]
    if not everything:
        return ""
    t0 = min(p.started_at for p in everything)
    t1 = max(p.started_at for p in everything)
    span = (t1 - t0).total_seconds() or 1.0
    maxn = max(len(w) for w in windows.values())

    def x(when: datetime) -> float:
        return round(left + (when - t0).total_seconds() / span * (right - left), 1)

    def rx(i: int) -> float:
        if maxn < 2:
            return round((left + right) / 2, 1)
        return round(left + i * (right - left) / (maxn - 1), 1)

    def y(rate: float) -> float:
        return round(bottom - rate * (bottom - top), 1)

    parts = []
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        cls = "grid dark" if frac == 1.0 else "grid"
        parts.append(
            f"<line class='{cls}' x1='{left}' y1='{y(frac)}' x2='{right}' y2='{y(frac)}'/>"
        )
        parts.append(
            f"<text class='axis' x='{left - 6}' y='{y(frac) + 3}' "
            f"text-anchor='end'>{_axis_pct(frac)}</text>"
        )

    time_ticks = []
    for i in range(5):
        when = t0 + (t1 - t0) * i / 4
        anchor = "start" if i == 0 else "end" if i == 4 else "middle"
        tick_x = x(when)
        time_ticks.append(
            f"<text class='axis' x='{tick_x}' y='{bottom + 20}' text-anchor='{anchor}'>"
            f"{when.strftime('%H:%M')}"
            f"<tspan x='{tick_x}' dy='12'>{when.strftime('%m-%d')}</tspan></text>"
        )
    parts.append(f"<g class='ticks-time' style='display:none'>{''.join(time_ticks)}</g>")
    run_ticks = []
    for i in sorted({round(k * (maxn - 1) / 4) for k in range(5)}):
        anchor = "start" if i == 0 else "end" if i == maxn - 1 else "middle"
        run_ticks.append(
            f"<text class='axis' x='{rx(i)}' y='{bottom + 20}' "
            f"text-anchor='{anchor}'>run {i + 1}</text>"
        )
    parts.append(f"<g class='ticks-run'>{''.join(run_ticks)}</g>")

    # Steady subjects first so movers draw over them.
    order = sorted(windows, key=lambda s: (s in colours, s))
    labels = []
    for subject in order:
        window = windows[subject]
        colour = colours.get(subject)
        cls = "tline" if colour else "tline steady"
        stroke = f" stroke='{colour}'" if colour else ""
        mover = " mover" if colour else ""
        series = [f"<g class='series{mover}' data-subject='{html.escape(subject)}'>"]
        if len(window) > 1:
            tpts = " ".join(f"{x(p.started_at)},{y(p.rate)}" for p in window)
            rpts = " ".join(f"{rx(i)},{y(p.rate)}" for i, p in enumerate(window))
            last = window[-1]
            tip = html.escape(
                f"{subject} — latest {_axis_pct(last.rate)} ({last.passed}/{last.total}) "
                f"over {len(window)} runs"
            )
            series.append(
                f"<polyline class='{cls}'{stroke} points='{rpts}' "
                f"data-tpts='{tpts}' data-rpts='{rpts}' data-tip='{tip}'/>"
            )
        changes = version_changes(window)
        for i, p in enumerate(window):
            # Steady series stay quiet: endpoint only — except a version change
            # or an errored run, which matter precisely when the score did not
            # move (a pin bump with no score change is the reassuring finding).
            if not colour and i < len(window) - 1 and p.run_id not in changes and not p.errored:
                continue
            series.append(
                _ov_dot(
                    p, x(p.started_at), rx(i), y(p.rate), colour, changes,
                    lone=len(window) == 1,
                )
            )
        series.append("</g>")
        parts.append("".join(series))
        if colour:
            last = window[-1]
            labels.append(
                (subject, colour, x(last.started_at), rx(len(window) - 1), y(last.rate), last)
            )

    parts.append(_edge_labels(labels))
    return (
        f"<svg class='ov' role='img' width='{_OV_W}' height='{_OV_H}' "
        f"viewBox='0 0 {_OV_W} {_OV_H}' aria-label='Pass rate over time for every subject; "
        "subjects that moved are coloured and labelled, steady subjects are grey'>"
        + "".join(parts)
        + "</svg>"
    )


def _ov_dot(
    p: Point,
    tx: float,
    rx: float,
    cy: float,
    colour: str | None,
    changes: dict[str, list[str]],
    lone: bool = False,
) -> str:
    moved = changes.get(p.run_id, [])
    tip = (
        f"{p.subject} — {p.started_at.strftime('%Y-%m-%d %H:%M')} — "
        f"{_axis_pct(p.rate)} ({p.passed}/{p.total})"
    )
    if p.errored:
        tip += f" — {p.errored} errored"
    if p.advisory_failed:
        tip += f" — {p.advisory_failed} advisory"
    tip += " — " + "; ".join(moved) if moved else " — no version change"
    coords = f"cx='{rx}' data-tx='{tx}' data-rx='{rx}' cy='{cy}'"
    if moved:
        dot = f"<circle class='pt vc' {coords} r='4'/>"
    elif p.errored:
        dot = f"<circle class='pt err' {coords} r='3.6'/>"
    else:
        fill = colour or "var(--steady)"
        # A single-run subject draws no line, so its only dot must read as a
        # marked point rather than a speck (RC1-314).
        dot = f"<circle class='pt' fill='{fill}' {coords} r='{3.5 if lone else 2}'/>"
    return (
        f"<g><title>{html.escape(tip)}</title>"
        f"<circle class='hit' {coords} r='7'/>{dot}</g>"
    )


def _edge_labels(labels: list[tuple[str, str, float, float, float, Point]]) -> str:
    """Right-gutter labels, staggered to a minimum vertical gap and joined to
    their endpoints by dashed leaders."""
    if not labels:
        return ""
    _, top, _, bottom = _OV_PLOT
    lo, hi = top + 6, _OV_H - _LABEL_GAP
    desired = [min(max(item[4], lo), hi) for item in labels]
    ys = _stagger(desired, _LABEL_GAP, lo, hi)
    out = []
    for (subject, colour, px, rpx, py, last), ly in zip(labels, ys, strict=True):
        name = html.escape(subject)
        out.append(
            f"<line class='leader' data-subject='{name}' x1='{rpx + 4}' "
            f"data-tx1='{px + 4}' data-rx1='{rpx + 4}' y1='{py}' "
            f"x2='{_OV_PLOT[2] + 8}' y2='{ly}'/>"
            f"<g class='slabel' data-subject='{name}'>"
            f"<text class='name' fill='{colour}' x='{_OV_PLOT[2] + 12}' y='{ly + 3}'>{name}</text>"
            f"<text class='val' x='{_OV_PLOT[2] + 12}' y='{ly + 16}'>"
            f"{_axis_pct(last.rate)} {last.passed}/{last.total}</text></g>"
        )
    return "".join(out)


def _stagger(desired: Sequence[float], gap: float, lo: float, hi: float) -> list[float]:
    """Nudge label positions apart until no two are within `gap`, keeping each
    as close to its desired position as the constraint allows."""
    order = sorted(range(len(desired)), key=lambda i: desired[i])
    ys = list(desired)
    prev = lo - gap
    for i in order:
        ys[i] = max(ys[i], prev + gap)
        prev = ys[i]
    prev = hi + gap
    for i in reversed(order):
        ys[i] = min(ys[i], prev - gap)
        prev = ys[i]
    return ys


# --- per-subject sections -------------------------------------------------

#: Sparkline geometry in viewBox units: full column width so 40 runs at the
#: display limit stay separable. Plot rectangle is (left, top, right, bottom).
_SPARK_W, _SPARK_H = 900, 122
_PLOT = (46, 16, 880, 92)

#: Below this many runs a line chart is a shape with no trend in it — two
#: points always draw a clean slope. Draw nothing rather than something
#: misleading.
_MIN_SPARK_RUNS = 3


def _axis_bottom(min_rate: float) -> float:
    """The y-axis floor: the top is always pinned at 100%, the bottom hugs the
    data in 0.05 steps with headroom.

    Hugging both ends (the RC1-268 design) let a flat 100% render mid-chart,
    visually identical to a subject oscillating — the printed labels did not
    undo the impression. Pinning the top puts "perfect" on the ceiling and
    makes a dip a real dip; printing both labels now defends only the bottom.
    """
    return min(0.95, max(0.0, math.floor((min_rate - 0.08) * 20) / 20))


def _section(
    subject: str,
    series: Sequence[Point],
    window: Sequence[Point],
    colours: dict[str, str],
    descriptions: dict[str, str],
    now: datetime | None,
) -> str:
    changes = version_changes(list(window))
    shown = list(reversed(window))
    dropped = len(series) - len(window)
    spark = _sparkline(subject, window, changes, colours.get(subject))

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
    desc = descriptions.get(subject)
    desc_html = f"<p class='desc'>{html.escape(desc)}</p>" if desc else ""
    return (
        f"<section class='subject' id='s-{html.escape(subject)}'>"
        + _section_head(subject, window, now)
        + desc_html
        + spark
        + "<div class='wrap'><table>"
        "<tr><th>run</th><th>pass rate</th><th>cases</th><th>model</th>"
        "<th>prompt</th><th>cost</th><th>changed since previous</th></tr>"
        + "".join(rows)
        + f"</table></div>{note}</section>"
    )


def _section_head(subject: str, window: Sequence[Point], now: datetime | None) -> str:
    """Name, model, run count, freshness — then the current rate and its
    direction, so the reader knows which way things went before the chart is
    read.

    Freshness is stated in words (RC1-314) because the run-number axis no
    longer implies it: a subject nobody has measured lately must say so
    rather than look like a subject measured yesterday.
    """
    last = window[-1]
    parts = [f"<h2>{html.escape(subject)}</h2>"]
    if last.model:
        parts.append(f"<span class='pill'>{html.escape(_short(last.model))}</span>")
    parts.append(f"<span class='runsn'>{len(window)} run(s)</span>")
    if now is not None and last.started_at != datetime.min:
        parts.append(f"<span class='runsn'>· {_freshness(last.started_at, now)}</span>")
    parts.append("<span class='grow'></span>")
    cls = "ok" if last.rate >= 0.9 else "warn" if last.rate >= 0.5 else "bad"
    parts.append(f"<span class='now {cls}'>{_axis_pct(last.rate)}</span>")
    if len(window) >= 2:
        delta = (last.rate - window[-2].rate) * 100
        if abs(delta) < 0.05:
            parts.append("<span class='chip flat'>no change</span>")
        else:
            arrow = "up" if delta > 0 else "down"
            parts.append(f"<span class='chip {arrow}'>{delta:+.1f} pts</span>")
    return "<div class='shead'>" + "".join(parts) + "</div>"


def _freshness(last: datetime, now: datetime) -> str:
    """Day-granular on purpose: the page answers "is anyone measuring this",
    not "at what hour". Dates are compared in each timestamp's own zone —
    off-by-hours is fine at this resolution and keeps naive records safe to
    subtract."""
    days = (now.date() - last.date()).days
    if days <= 0:
        return "last run today"
    if days == 1:
        return "last run yesterday"
    return f"last run {days} days ago"


def _sparkline(
    subject: str, series: Sequence[Point], changes: dict[str, list[str]], colour: str | None
) -> str:
    """Pass rate over the table's window, oldest → newest, as inline SVG.

    Three dot states (see the page legend): an ordinary run, a run with errored
    cases (hollow, in the bad colour — an eval that crashed is not an eval that
    scored low), and a run where a version changed (accented — the attribution
    the page exists for). Every dot carries an SVG `<title>`, a native hover
    tooltip with no JavaScript.
    """
    if len(series) < _MIN_SPARK_RUNS:
        return ""
    left, top, right, bottom = _PLOT
    lo = _axis_bottom(min(p.rate for p in series))
    hi = 1.0
    mid = (lo + hi) / 2
    stroke = colour or "var(--steady)"

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
        if point.errored:
            tip += f" — {point.errored} errored"
        if moved:
            tip += " — " + "; ".join(moved)
        if moved:
            dot = f"<circle class='pt vc' cx='{x(i)}' cy='{y(point.rate)}' r='4'/>"
        elif point.errored:
            dot = f"<circle class='pt err' cx='{x(i)}' cy='{y(point.rate)}' r='3.6'/>"
        else:
            dot = f"<circle class='pt' fill='{stroke}' cx='{x(i)}' cy='{y(point.rate)}' r='2.6'/>"
        dots.append(
            f"<g><title>{html.escape(tip)}</title>"
            f"<circle class='hit' cx='{x(i)}' cy='{y(point.rate)}' r='8'/>{dot}</g>"
        )

    path = " ".join(f"{x(i)},{y(p.rate)}" for i, p in enumerate(series))
    fill = ""
    if colour:
        # Movers only: under a flat line the fill is a slab that reads as a
        # bar chart, so steady subjects get none.
        fill = (
            f"<polygon fill='{stroke}' fill-opacity='0.1' "
            f"points='{path} {x(len(series) - 1)},{bottom} {x(0)},{bottom}'/>"
        )
    first, last = series[0], series[-1]
    return (
        f"<svg class='spark' role='img' width='{_SPARK_W}' height='{_SPARK_H}' "
        f"viewBox='0 0 {_SPARK_W} {_SPARK_H}' "
        f"aria-label='Pass rate per run for {html.escape(subject)}, oldest to newest'>"
        f"<line class='grid dark' x1='{left}' y1='{top}' x2='{right}' y2='{top}'/>"
        f"<line class='grid mid' x1='{left}' y1='{y(mid)}' x2='{right}' y2='{y(mid)}'/>"
        f"<line class='grid' x1='{left}' y1='{bottom}' x2='{right}' y2='{bottom}'/>"
        f"<text class='axis' x='{left - 6}' y='{top + 3}' text-anchor='end'>{_axis_pct(hi)}</text>"
        f"<text class='axis' x='{left - 6}' y='{y(mid) + 3}' "
        f"text-anchor='end'>{_axis_pct(mid)}</text>"
        f"<text class='axis' x='{left - 6}' y='{bottom + 3}' "
        f"text-anchor='end'>{_axis_pct(lo)}</text>"
        f"<text class='axis' x='{left}' y='{bottom + 16}'>"
        f"{first.started_at.strftime('%Y-%m-%d %H:%M')}</text>"
        f"<text class='axis' x='{right}' y='{bottom + 16}' text-anchor='end'>"
        f"{last.started_at.strftime('%Y-%m-%d %H:%M')}</text>"
        + fill
        + f"<polyline class='tline' stroke='{stroke}' points='{path}'/>"
        + "".join(dots)
        + "</svg>"
    )


def _legend(windows: dict[str, list[Point]]) -> str:
    """The dot vocabulary, plus the attribution status said in words.

    When `version_changes` fires nowhere in the window, the accent dot never
    renders and the page's central claim is invisible — the legend says so
    rather than staying silent. Same rule as the display cap: an absence the
    reader cannot distinguish from an omission must be stated.
    """
    marked = sum(len(version_changes(w)) for w in windows.values())
    if marked:
        attrib = f"{marked} run(s) marked above carry a version change."
    else:
        attrib = "No version changes in this window — every movement above is unattributed."
    return (
        "<footer class='legend'>"
        "<span><span class='dot run'></span>run</span>"
        "<span><span class='dot err'></span>cases errored</span>"
        "<span><span class='dot vc'></span>model / prompt / code version changed</span>"
        f"<span class='attrib'>{attrib}</span></footer>"
    )


def _axis_pct(rate: float) -> str:
    return f"{rate * 100:.1f}".rstrip("0").rstrip(".") + "%"


def _rate_cell(point: Point) -> str:
    """Rate colour says how good; the bad colour is reserved for errors, so a
    low score and a crashed run stop looking like the same finding."""
    pct = point.rate * 100
    width = max(1, round(point.rate * 60))
    cls = "ok" if point.rate == 1 else "warn"
    return (
        f"<span class='bar {'' if point.rate == 1 else 'warn'}' style='width:{width}px'></span> "
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
