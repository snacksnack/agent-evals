# agent-evals

A regression suite for LLM systems. Frozen cases, scored characteristics, a
judge that had to earn the right to fail a build, and run records that carry
what each run cost.

Built because five LLM systems were in production across three repos and nothing
answered the question *"how do you know the output is any good?"*

## The idea in one paragraph

A **subject** is a system under test. It runs against frozen **cases**, and each
output is scored on named **characteristics** — never against an expected string,
because generative output is legitimately variable and a case pinning an exact
phrase fails on a harmless rewording. Results go into an append-only **run
record** carrying the subject version, the model, the prompt version, the token
cost and the latency, so a regression can be *attributed* rather than guessed at.

```python
from agent_evals import Case, CaseResult, CharacteristicResult, RunRecord, Usage

case = Case(
    id="week-with-a-slip",
    input={"facts": facts},
    expect=("states-the-required-facts", "no-unsupported-claims"),
)
```

## What is actually checkable without a model

Most of what matters is exact, and checking it costs nothing:

```python
from agent_evals import groundedness

report = groundedness.check(output_text, facts)
report.grounded              # False if a claim has no support in the input
report.hallucination_rate    # unsupported / checkable, not a boolean
```

It checks ticket keys, dates in either format, day-counts, and — the one that
catches real damage — a health or severity state the facts contradict. Softening
a red week to "at some risk" is a violation *even when every number is correct*.

**Precision over recall, deliberately.** A checker that flags correct output gets
muted, and a muted checker catches nothing. Five rounds of false positives went
into the current rules, every one found by running against real output rather
than by a passing test:

| Flagged wrongly | Why it was wrong |
| --- | --- |
| `August 3, 2026` as invented | The date was inside `period_label: "Week of 2026-08-03"` — the matcher only looked at whole field values |
| `"core engineering remains on track"` | True, and about part of the work, not overall health |
| `"health is green, with no red flags"` | Negated — a green sentence containing the word "red" |
| `"reflects a healthy buffer"` | Different word sense |
| `"no tasks slipped"` on a quiet week | Correct reporting, flagged as manufactured activity |

## Judging what cannot be checked

Some claims need judgement — *"the team has absorbed the slip"*, *"reflecting
improved schedule buffer"*. For those there is an LLM judge, and it does not get
trusted for free.

```bash
evals label --dimension no-unsupported-claims --scorer human --limit 24
evals construct --scorer human      # tripwire: is this label set usable at all?
evals calibrate --scorer human      # weighted kappa, with a bootstrap interval
```

Three things this library insists on, each bought the hard way:

**Raw agreement flatters.** If 80% of outputs are fine, a judge that says "fine"
to everything agrees 80% of the time having measured nothing. Agreement is
Cohen's kappa, linear-weighted because the scale is ordinal.

**The interval decides, not the point estimate.** One calibration measured κ 0.82
over 12 items; doubling to 24 moved it to 0.66 with a third of the bootstrap
distribution below the gating floor. A point estimate above the floor is not the
same as having cleared it.

**A construct check that needs no labels.** If part of your corpus is degraded on
purpose, whether a scorer ranks the clean above the planted is knowable without
anyone labelling anything — the same instinct as planted-defect recall. It runs
in seconds and would have discarded three unusable label sets immediately.
Passing it earns no gating rights; it only shows the scorer is measuring
something real.

## Advisory is a first-class state

```python
CharacteristicResult(name="reaches-it-without-a-detour", passed=False, advisory=True)
```

An advisory result is reported in every run and can never fail a build.
`CaseResult.passed` excludes them. Most judge-scored dimensions live there,
because *a flaky gate gets disabled within a week, which is worse than no gate*.

## What this library does not carry

Subjects, fixtures, credentials, and configuration — all of which belong with the
code under test. An eval is a test, and tests live beside the thing they test;
what travels is only the part that is not specific to any one system. The judge
takes a resolved API key as an argument rather than reading an environment
variable, because each consumer prefixes its own differently and a library that
reached for one would work in exactly one repo.

## Install

Consumed by git ref. It is a library — nothing here is deployed.

```toml
dependencies = [
    "agent-evals @ git+https://github.com/snacksnack/agent-evals@v0.1.0",
]
```

## Status

Extracted from `launch-planner-agent`, where it currently measures MCP tool
routing, status narratives, and deterministic groundedness across four subjects.
The design decisions and their reasoning live in that repo's `docs/decisions.md`
(ADR-0030 through ADR-0034) and `docs/judging.md`.
