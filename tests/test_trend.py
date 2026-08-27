"""The trend view, including the two things it must not get wrong (RC1-255).

It exists to answer one question — a score moved, what moved with it — so the
tests that matter are: does it attribute correctly, and does it refuse to lie
about what it is showing.
"""

from __future__ import annotations

import re
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
    """The constraint as restated by RC1-271: one file, no external request,
    no credential — not "no <script>". The page's own inline script satisfies
    all three; a script src, webfont or remote image would fail here."""
    record = _record(0)
    record["subject_version"]["subject"] = "<script>alert(1)</script>"
    page = trend.render([record])
    assert "<script>alert(1)</script>" not in page, "subject names are data, not markup"
    assert "&lt;script&gt;" in page
    assert "http" not in page, "no external requests of any kind"
    assert "src=" not in page, "nothing loads from anywhere"


def test_the_interactions_degrade_without_disappearing():
    """With scripting disabled the static document must already contain every
    chart, label and value — the inline script only reveals its own controls
    and swaps coordinates that are all pre-rendered (RC1-271)."""
    page = trend.render([_record(i) for i in range(3)])
    static = page[: page.index("<script>")]
    assert "class='ov'" in static and "class='spark'" in static, "charts are server-drawn"
    assert "data-rpts=" in static, "the run-number layout is pre-rendered, not computed"
    assert "class='ticks-run'" in static
    assert "hover a line — click to isolate a subject" in static, "readout resting state"
    assert "hidden>" in static, "js-only controls ship hidden rather than dead"


def test_the_controls_are_real_buttons():
    """Keyboard reachability comes from using actual <button> elements — the
    toggles and every legend chip — not click handlers on divs."""
    page = trend.render([_record(i) for i in range(3)])
    assert ">movers only</button>" in page
    assert ">by run #</button>" in page
    assert "class='chipbtn'" in page and "aria-pressed" in page


def _svg(page):
    """The one sparkline on a single-subject page, or None if there isn't one.

    The overview chart also draws an `<svg>`, so this keys on the spark class
    rather than the first tag on the page.
    """
    if "class='spark'" not in page:
        return None
    start = page.index("class='spark'")
    return page[start : page.index("</svg>", start)]


def test_a_sparkline_appears_only_at_three_or_more_runs():
    """Two points always draw a clean slope — below the threshold the chart
    would be a shape with no trend in it, so nothing is drawn instead (RC1-268)."""
    assert _svg(trend.render([_record(i) for i in range(2)])) is None
    assert _svg(trend.render([_record(i) for i in range(3)])) is not None


def test_sparkline_marks_exactly_the_runs_where_a_version_changed():
    records = [
        _record(0),
        _record(1, prompt="p-2"),
        _record(2, prompt="p-2"),
        _record(3, prompt="p-2", model="claude-opus-5"),
    ]
    svg = _svg(trend.render(records))
    assert svg.count("pt vc") == 2, "one marker per version-change run, no more"
    assert "prompt: p-1 → p-2" in svg, "the marker's title names what changed"
    assert "model: claude-sonnet-5 → claude-opus-5" in svg


def test_sparkline_axis_bounds_are_printed_not_implied():
    records = [_record(0, passed=1, failed=1), _record(1), _record(2)]
    svg = _svg(trend.render(records))
    assert ">40%<" in svg and ">100%<" in svg, "floor hugs the data in 0.05 steps, top is 100%"


def test_the_axis_top_is_pinned_at_100_percent():
    """A flat perfect series sits on the plot ceiling, where perfect belongs
    (RC1-270). The RC1-268 hugged axis drew it mid-chart, visually identical
    to a subject oscillating — the printed labels did not undo the impression."""
    svg = _svg(trend.render([_record(i) for i in range(3)]))
    assert ">90%<" in svg and ">100%<" in svg, "the floor is derived, both bounds printed"
    top = float(trend._PLOT[1])
    assert svg.count(f"cy='{top}'") >= 3, "every 100% point sits on the top edge"


def test_the_sparkline_shows_the_same_window_as_the_table():
    """Chart and table must describe the same runs — a chart drawn from runs the
    table dropped would attribute a slope to rows the reader cannot see."""
    records = [_record(0, passed=0, failed=2)] + [_record(i) for i in range(1, 5)]
    svg = _svg(trend.render(records, limit=3))
    assert svg.count("class='pt") == 3, "one dot per displayed run"
    assert ">0%<" not in svg, "the dropped 0% run does not set the axis floor"


def test_a_run_with_errors_is_drawn_differently_from_a_low_score():
    """An eval that crashed and an eval that failed are different findings
    (RC1-270): errored runs draw hollow in the bad colour, ordinary runs
    filled. `stakeholder-status-email` at 0/4 with 4 errored must not look
    like a subject that merely scored zero."""
    svg = _svg(trend.render([_record(0), _record(1, passed=1, errored=3), _record(2)]))
    assert svg.count("pt err") == 1, "exactly the errored run is hollow"
    assert "3 errored" in svg, "...and its tooltip says how many"


def test_the_masthead_rate_is_the_latest_run_of_each_subject():
    """108/115-style aggregate: latest run per subject, pooled by cases — not
    an average of averages, and history does not dilute the present."""
    old_bad = _record(0, passed=0, failed=2)
    now_good = _record(1)  # 2/2
    other = _record(0, passed=1, failed=1)  # 1/2, only run
    other["subject_version"]["subject"] = "other"
    page = trend.render([old_bad, now_good, other])
    assert "3/4 cases" in page, "latest runs only: 2/2 and 1/2"
    assert ">75%</span>" in page
    assert "1/2 at 100%" in page


def test_the_overview_separates_movers_from_steady_subjects():
    """Movers get a colour and a right-edge label; steady subjects stay grey
    and unlabelled. Without the split, every line stacks on 100% and the chart
    is a thicket — with it, the grey lines still prove nothing is hidden."""
    moved = [_record(0), _record(1, passed=1, failed=1), _record(2)]
    steady = [_record(i) for i in range(3)]
    for r in steady:
        r["subject_version"]["subject"] = "steady"
        r["run_id"] += "-steady"
    page = trend.render(moved + steady)
    ov = page[page.index("class='ov'") : page.index("</svg>")]
    assert "tline steady" in ov, "the flat subject draws grey"
    assert ov.count("class='slabel'") == 1, "only the mover is labelled"
    assert "data-subject='demo'><text" in ov.replace("\n", "")


def test_overview_labels_are_never_stacked_closer_than_the_minimum_gap():
    """Six movers can all end within a few percent of each other; their
    right-edge labels stagger apart rather than overprinting."""
    records = []
    for i in range(6):
        name = f"subject-{i}"
        for n in range(3):
            r = _record(n, passed=19 if n else 18 - i, failed=1 if n else 2 + i)
            r["subject_version"]["subject"] = name
            r["run_id"] = f"{name}-{n}"
            records.append(r)
    page = trend.render(records)
    ys = sorted(float(m) for m in re.findall(r"class='name'[^>]* y='([\d.]+)'", page))
    assert len(ys) == 6
    gaps = [b - a for a, b in zip(ys, ys[1:], strict=False)]
    assert all(g >= trend._LABEL_GAP - 1e-6 for g in gaps), gaps


def test_the_overview_defaults_to_the_run_number_axis():
    """Runs are deliberate and sparse (RC1-314): on a time axis ten quiet days
    render as a stretch of nothing that reads as a broken chart. The time
    layout survives as the toggle, pre-rendered in `data-` attributes."""
    page = trend.render([_record(n) for n in (0, 1, 10)])  # irregular gaps
    ov = page[page.index("class='ov'") : page.index("</svg>")]
    m = re.search(r"points='([^']*)' data-tpts='([^']*)' data-rpts='([^']*)'", ov)
    assert m.group(1) == m.group(3), "the server-drawn line uses the run layout"
    assert m.group(2) != m.group(3), "...and the time layout is genuinely different"
    assert "<g class='ticks-run'>" in ov, "run ticks show without JavaScript"
    assert "class='ticks-time' style='display:none'" in ov
    assert "id='b-run' aria-pressed='true'" in page, "the pressed control matches"


def test_each_subject_states_when_it_last_ran():
    """Freshness in words (RC1-314): the run-number axis no longer implies it,
    and a subject nobody measured lately must say so. `now` is an argument,
    not a clock read — without it the label is absent and `render` stays pure."""
    records = [_record(i) for i in range(3)]
    page = trend.render(records, now=_WHEN + timedelta(days=10, hours=3))
    assert "last run 10 days ago" in page
    assert "last run today" in trend.render(records, now=_WHEN + timedelta(hours=3))
    assert "last run yesterday" in trend.render(records, now=_WHEN + timedelta(days=1, hours=3))
    assert "last run" not in trend.render(records)


def test_a_single_run_subject_is_a_marked_point_not_a_speck():
    """A one-run subject draws no line, so its only dot is enlarged in the
    overview rather than rendering at the steady endpoint size (RC1-314)."""
    lone = _record(0)
    lone["subject_version"]["subject"] = "lone"
    lone["run_id"] = "lone-0"
    page = trend.render([lone] + [_record(i) for i in range(3)])
    ov = page[page.index("class='ov'") : page.index("</svg>")]
    assert "r='3.5'" in ov, "the lone dot is enlarged"
    without = trend.render([_record(i) for i in range(3)])
    assert "r='3.5'" not in without, "multi-run subjects keep the quiet endpoint dot"


def test_the_legend_says_when_no_movement_is_attributed():
    """`version_changes` firing nowhere means the accent dot never renders and
    the page's central claim is invisible — the legend states the absence
    rather than staying silent (RC1-270, same rule as the display cap)."""
    quiet = trend.render([_record(i) for i in range(3)])
    assert "No version changes in this window" in quiet
    loud = trend.render([_record(0), _record(1, prompt="p-2"), _record(2, prompt="p-2")])
    assert "No version changes" not in loud
    assert "1 run(s) marked above carry a version change" in loud


def test_a_subject_description_renders_when_supplied_and_not_otherwise():
    """One sentence saying what a subject measures (RC1-272). It is copy, so
    it arrives as an argument — the records and the store know nothing of it —
    and a subject without an entry renders no line rather than a placeholder."""
    records = [_record(i) for i in range(3)]
    told = trend.render(records, descriptions={"demo": "What this measures."})
    assert "<p class='desc'>What this measures.</p>" in told
    assert "title='What this measures.'" in told, "the legend chip explains the name on hover"
    untold = trend.render(records, descriptions={"other-subject": "irrelevant"})
    assert "class='desc'" not in untold and "irrelevant" not in untold


def test_a_description_is_escaped_as_data():
    page = trend.render(
        [_record(0)], descriptions={"demo": "<img onerror=x> & \"quotes\""}
    )
    assert "<img" not in page
    assert "&lt;img" in page


def test_an_empty_log_renders_a_page_rather_than_crashing():
    page = trend.render([])
    assert "No run records found" in page


def test_a_record_missing_optional_fields_still_renders():
    """The log outlives schema changes — that is why it is kept as raw dicts."""
    minimal = {"run_id": "x", "subject_version": {"subject": "s"}, "results": []}
    [point] = trend.points([minimal])
    assert point.model is None and point.prompt_version is None
    assert "s" in trend.render([minimal])
