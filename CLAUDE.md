# CLAUDE.md — conventions for reviewers and AI sessions

One page, under 6,000 characters so the PR review agent reads it whole. The
reasoning behind each rule is in the README and `docs/`.

## What this is

A **library** for evaluating LLM systems — frozen cases, characteristics scored
rather than strings asserted, a judge that had to earn the right to fail a
build, and append-only run records carrying model, prompt version, cost and
latency so a regression can be *attributed*. Nothing here is deployed.

Five repos consume it, fifteen subjects between them, each pinning a git tag:
`agent-evals[sql] @ git+…/agent-evals@v0.6.1`. That pin is the fact a reviewer
most needs: a harness change is deliberately **two PRs** — one here plus a tag,
one bump per consumer — and a pin bump that moves a score is itself a finding
(RC1-261).

## Layout

```
agent_evals/
  case runner record     the core types and the run loop
  groundedness           deterministic checks; no model
  judge rubric           the LLM judge and its scoring rubric
  agreement labelling    kappa, bootstrap, label collection
  construct              label-free tripwire on a scorer
  pricing budget         the price snapshot and declared ceilings
  sql_store              the shared Postgres store
  trend trend_cli        the published quality-trend page
  llmobs seeds           Datadog LLM Obs wiring; corpus seeds
scripts/  scheduled_eval.sh, publish_trend.sh, install_launchd.sh
launchd/  the daily and weekly plists
docs/     measuring.md (the runbook), trend.md (the store and the page)
tests/    pytest, offline except the Postgres store tests
```

## Conventions (hold a change to these)

- **Never assert an expected string.** Generative output is legitimately
  variable; a case pinning a phrase fails on a harmless rewording. Score named
  characteristics instead.
- **Advisory is a first-class state.** `advisory=True` is reported in every run
  and can never fail a build; `CaseResult.passed` excludes it. Most judge-scored
  dimensions live there, because a flaky gate gets disabled within a week —
  worse than no gate.
- **The judge earns gating rights, never gets them free.** Agreement is
  linear-weighted Cohen's kappa; raw agreement flatters a judge that says "fine"
  to everything. **The interval decides, not the point estimate** — κ 0.82 over
  12 items became 0.66 over 24. `construct` needs no labels and earns no gating
  rights; it only shows the scorer measures something real.
- **Precision over recall in `groundedness`.** A checker that flags correct
  output gets muted, and a muted checker catches nothing. Five rounds of real
  false positives shaped these rules — read the README table before loosening a
  matcher.
- **An unknown model price raises** (`UnknownModelPrice`), never defaults to
  zero: a silent $0.00 makes a subject look free forever after a model rename.
  Prices are a **local snapshot** with an `AS_OF` date, so old runs stay
  comparable.
- **Cost is four token counts, not two.** `input_tokens` is only the *uncached*
  remainder; cache writes bill 1.25x and reads 0.1x. Pricing `input_tokens`
  alone understated PR reviews by ~60% (RC1-392). The 1-hour TTL is deliberately
  not modelled.
- **The bill is the price ground truth** — the Anthropic Console *Cost* CSV
  export, never Datadog's estimated cost, which is another price table x tokens
  (RC1-401).
- **No subjects, fixtures, credentials or config live here**; they belong with
  the code under test. The judge takes a **resolved API key as an argument**,
  never an environment variable. Ceilings live with the consumer, and
  `Ceiling.note` is required because a limit must be *measured, not guessed*.
- **A budget breach is a finding, not an exception**, and advisory — it lands
  beside the quality findings, because "can this subject move to a cheaper
  model" is answered by cost and quality together.
- **Tracing is decoration.** `llmobs.enable()` is a no-op without the `llmobs`
  extra and `DD_API_KEY`; failing to start tracing is a decline, never an error.
  A billed run must never die for observability.
- **An unreachable store is a hard failure**, not a note — rendering fewer
  records than exist understates a trend. Only an *empty* store is a note.
- **Suite exit 1 (cases failed) or 2 (cases errored) is a finding**, not a
  wrapper failure: the run is recorded, a sweep continues, the page publishes.
  Scheduled jobs skip loudly on a missing key — a silently skipped job looks
  identical to a passing one.

## Testing

- `uv run --extra dev pytest` from the repo root (`testpaths = tests`;
  `addopts = "-q"` already, so a second `-q` hides the summary line).
- Offline except `test_sql_store.py`, which wants `EVAL_TEST_DATABASE_URL`. CI
  gives it a real Postgres 16 service container, so the suite stays
  credential-free — no vendor, no secret.
- Ruff: `py312`, line length 100, rules `E F I UP B SIM`, with
  `known-first-party = ["agent_evals"]` stated rather than inferred.

## Commands

```bash
uv run --extra dev pytest                     # the suite
uv run --extra dev ruff check .               # lint
uv run python -m agent_evals.trend_cli --out site/index.html
scripts/publish_trend.sh                      # render + push to gh-pages
scripts/scheduled_eval.sh daily|weekly        # what launchd runs
```

`EVAL_DATABASE_URL` lives in `~/.zshrc` only, never in CI: without it a suite
forks history into local JSONL, so the wrapper refuses. The page is rendered and
published from a developer machine, never CI.

CI runs Python **3.12**; the local venv resolves 3.13.5 and there is no
`.python-version` yet (RC1-386) — run everything through `uv run`.

## Workflow

One branch per ticket, `rc1-NNN-slug`; never commit on `main`. Commit subject
`RC1-NNN: what changed`, short body, **no Co-Authored-By trailer**. Claude opens
the PR; Reid merges. A release is a version bump, a tag, then one pin-bump PR
per consuming repo.
