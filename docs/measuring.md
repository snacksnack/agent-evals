# Taking a measurement — the runbook (RC1-264)

Two steps, always the same two, whichever repo is being measured:

1. **Run the suite** in the consumer repo (table below).
2. **Publish the page** from this repo: `cd agent-evals && ./scripts/publish_trend.sh`.

The machine that just wrote records is the machine that rebuilds the page —
writes never happen from CI, so every point on the
[trend page](https://snacksnack.github.io/agent-evals/) exists because a human
chose to take a measurement (`docs/trend.md` records why).

## Prerequisites, once per machine

**`EVAL_DATABASE_URL`** in a shell profile (`~/.zshrc`), and nowhere else — one
copy outside every repo, never a repo `.env` (RC1-263). Get it with:

```bash
heroku config:get DATABASE_URL --app reid-eval-store
```

`reid-eval-store` is a placeholder app holding the dedicated eval Postgres;
it serves nothing and is attached to nothing user-facing. Heroku rotates
credentials during maintenance, so an auth failure usually means "re-run the
command above and update the profile", not a leak.

Without the variable set, runs land in the repo's local
`./eval-runs/runs.jsonl`. That is the intended mode for iterating on cases —
and the wrong one for a measurement you meant to keep, because the local file
and the shared store are separate histories.

**An API key**, for the billed suites only. Each repo resolves its own —
that asymmetry is deliberate (ADR-0035; the library reads no environment
variable), so the table below is the one place it is written down.

## The suites

Run each from its repo root.

| Repo | Suite command | Free half | Key, and where it is read from |
| --- | --- | --- | --- |
| [`launch-planner-agent`](https://github.com/snacksnack/launch-planner-agent) | `uv run evals run <subject>` | `groundedness`, `status-narrative-fallback`, `health`, `spec-structural` | `LPA_ANTHROPIC_API_KEY`, from `.env` |
| [`tpm-automation-platform`](https://github.com/snacksnack/tpm-automation-platform) | `python -m evals run drift-digest` | `drift-digest-allclear`, `kpi-ledger` (both gate CI; RC1-300) | `ANTHROPIC_API_KEY`, from `.env` |
| [`pr-request-agent`](https://github.com/snacksnack/pr_agent) | `python -m evals` | `--list` (corpus only) | `ANTHROPIC_API_KEY`, from `.env` |
| [`n8n-stakeholder-status-email`](https://github.com/snacksnack/n8n-stakeholder-status-email) | `python -m evals` | `pytest` (layer 1) | `ANTHROPIC_API_KEY`, exported — no `.env` is read |
| [`n8n-concert-intelligence`](https://github.com/snacksnack/n8n-concert-intelligence-agent) | `python -m evals` | `pytest` (layer 1) | `ANTHROPIC_API_KEY`, exported — no `.env` is read |
| [`ai-incident-summarizer`](https://github.com/snacksnack/ai-incident-summarizer) (local dir `ai_powered_incident_alert_summarizer`) | `python -m evals` | — | `ANTHROPIC_API_KEY`, exported — the Lambda uses Secrets Manager, the eval deliberately doesn't touch AWS (RC1-267) |

`launch-planner-agent`'s billed subjects are `tool-selection`,
`status-narrative`, `work-breakdown`, `dependency`, `raid` and `spec-review`
(RC1-292 — planted-defect recall per rubric category over the spec-gate corpus,
plus false-positive restraint on the good spec and the fabricated-quote rate);
the other four repos have one billed subject each. A full sweep of every billed suite costs a few dollars,
and every record carries its own exact cost, so the running total is a query
rather than a guess.

Then publish:

```bash
cd agent-evals && ./scripts/publish_trend.sh
```

The script renders `site/index.html` from the store and force-pushes it as a
single parentless commit to `gh-pages`. Nothing else to do; there is no CI
step, by design.

## Scheduled runs (RC1-315)

launchd on this machine runs `scripts/scheduled_eval.sh` — the free
`kpi-ledger` suite daily at 09:00 and the full sweep above Mondays at 09:30 —
publishing the page after each. Install with `scripts/install_launchd.sh`;
logs are in `~/Library/Logs/agent-evals/`. Change-triggered manual runs
remain the primary measurement; `docs/trend.md` ("Scheduled runs: an
amendment, not a reversal") reconciles this with the never-CI rule.

## Reading the result

Every runner exits CI-shaped: `0` every case passed, `1` a case failed its
characteristics — the subject answered badly — and `2` a case errored, meaning
the subject produced nothing to score. `2` outranks `1` deliberately: "the
thing under test is broken" and "the thing under test answered badly" need
different people looking at them.

## When it fails

**The run refuses to start, naming the store.** An unreachable store fails
the run loudly rather than falling back to the local file — a silent fallback
would fork the record history. Check, in order: is `EVAL_DATABASE_URL` set in
*this* shell (it lives in the profile, so a stale terminal or an IDE-spawned
shell may not have it); has Heroku rotated the credential (`heroku
config:get` again); is the addon reachable at all (`heroku pg:info --app
reid-eval-store`).

**`CredentialShapedRecord`.** The record contained something shaped like a
real provider credential, and the write was refused before it reached any
store. Drop the record and fix the fixture so the planted value is one no
scanner claims — never allowlist (see `agent_evals/record.py` for why).

**`DuplicateRunId`.** Two records tried to share an id, which would make
report-by-id silently ambiguous. Re-run the suite; ids are generated fresh
per run.

**`publish_trend.sh` fails.** It needs the same `EVAL_DATABASE_URL`, plus
push access to this repo. A page that publishes but looks wrong is a
different problem — `docs/trend.md` says what the page claims and does not.

## Where knowledge lives

Consolidation means pointing, not moving (RC1-264) — so this is the map, and
each piece has exactly one home:

- **How quality is measured** — this repo's [README](../README.md).
- **The store decision, the git post-mortem, what the trend page claims** —
  [`docs/trend.md`](trend.md).
- **Judge validation: the kappa numbers, the gating floor, the limits** —
  [`launch-planner-agent/docs/judging.md`](https://github.com/snacksnack/launch-planner-agent/blob/main/docs/judging.md).
- **Design history** — ADR-0030 through ADR-0037 in
  [`launch-planner-agent/docs/decisions.md`](https://github.com/snacksnack/launch-planner-agent/blob/main/docs/decisions.md).
- **Each repo's subjects, cases and fixtures** — that repo, beside the code
  they test (ADR-0030).
