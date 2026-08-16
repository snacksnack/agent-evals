# The trend view, and where the records live (RC1-255, RC1-263)

**Decision.** Run records live in a dedicated Heroku Postgres database, read
and written through `SqlRunStore`. A pure renderer in `agent_evals/trend.py`
turns them into one self-contained HTML page, rendered and published from a
developer machine to GitHub Pages. CI holds no credential.

This replaces a design where consumer repos committed their records as JSONL
and this repo fetched five public files to build the page (RC1-255). That
design was tried, worked once, and was abandoned by RC1-263. The renderer
survived unchanged; only the source moved — which is what the
records-as-raw-dicts seam was for.

## Why git was tried, and why it was abandoned

The records were already an append-only log. Git stores an append-only log,
with history, blame and diff for free, and every consumer repo is public — so
the page could be built with no token, no service, and no moving part. That
reasoning was not stupid, which is exactly why it is worth writing down how it
failed. Every piece of evidence arrived after the decision, and every piece
was storage-shaped:

- **A run record inherits the sensitivity of whatever it observed.**
  `pr_agent`'s eval plants a credential on purpose and the review agent quotes
  it back in a finding. While the fixture used a realistic `sk_live_...`,
  GitHub push protection refused the log — correctly, twice over.
- **Machine-generated artifacts do not belong in a source diff.** The PR
  review agent blocked a records PR because `runs.jsonl` was too large to
  appear in the diff and it refused to approve what it could not read. A full
  audit found the log clean, so the finding was a false positive — but the
  caution was right.
- **Git stores the whole blob on every commit that touches a file.** An
  ever-growing JSONL rewrites itself on every append, and the repo carries a
  full copy each time.
- **The workarounds became the signal.** Monthly rotation in five repos, a
  directory-listing fetch, a credential-scanning test, six `.gitignore`
  edits. RC1-248 rejected a design on exactly this smell — plumbing invented
  to work around a storage choice is the storage choice announcing it is
  wrong — and the rule was not applied in time.
- **It is the wrong data structure for the question.** "Every run where a
  subject regressed after a model change" is a query. Committed JSONL cannot
  answer it without loading everything.

## The store

A **second** Heroku Postgres addon, deliberately not the resume site's
database: that one holds real visitor data, and sharing it would put a
credential that reaches PII into every environment the eval credential
touches. A separate addon makes the isolation a fact about the credential
rather than a fact about a `GRANT` being right — the failure mode of a
restricted role on a shared database is a subtly over-broad grant, and that
failure is silent.

- Records land as **JSONB plus extracted columns** (`subject`, `model`,
  `prompt_version`, `code_version`, `started_at`). `trend.points()` keeps
  working off raw dicts, and the regression question above is an indexed
  query.
- **Append-only, enforced by the database.** A `BEFORE UPDATE OR DELETE`
  trigger raises — the guarantee the JSONL store could only hold structurally
  (see `agent_evals/sql_store.py`, and ADR-0012 for the SQLite original).
- The JSONL `RunStore` **stays** as the local default, behind the same
  `append` / `get` / `all` interface. It keeps the seam honest and needs no
  database to iterate against.
- The credential lives in **one place**, a shell profile outside any repo:
  `EVAL_DATABASE_URL`. Five repo `.env` files would be five copies drifting
  apart. Heroku rotates credentials during maintenance — an auth failure
  usually means "re-run `heroku config:get DATABASE_URL`", not a leak.

## Who writes, who reads, who publishes

Writes happen from a developer machine running a suite — **never CI**. So
every record exists because a human chose to take a measurement, and the
machine that just wrote records is the machine that rebuilds the page:
`scripts/publish_trend.sh` renders `site/index.html` and force-pushes it as a
single parentless commit to `gh-pages`, which Pages serves directly.

That kills the last credential CI would have needed. The original RC1-263
plan kept a "read path" secret in a Pages workflow — but on Heroku's
Essential tier the only credential is the owning one, so a read path would in
fact have been a write-capable secret sitting in Actions. Rendering at write
time needs none, and the daily rebuild it replaces was an artifact of the git
design: with a database, a schedule rebuilds unchanged data.

The library's own CI tests `SqlRunStore` against a Postgres **service
container** — a real Postgres, triggers included, no secret, no vendor. The
suite stays credential-free, which is the property ADR-0031 protects.

## The write-time sensitivity guard

The repo-scanning test the git design needed becomes a refusal at the moment
of writing: a record containing a provider-shaped credential raises
`CredentialShapedRecord` before it reaches either store. Drop the affected
record and fix the fixture rather than allowlisting it — a planted secret
must be a value no real scanner claims.

## What the page claims, and what it does not

One question: **a score moved — what moved with it?** Every point carries its
three versions, and a row is marked where any of them changed since the
previous run. A drop beside a mark has a suspect. A drop with nothing marked
means something moved that the record does not carry, and inventing an
attribution there would be worse than showing none.

It does not do significance testing. With runs numbering in the dozens, it
should not pretend to.

**Advisory results never count against a pass rate.** ADR-0033 and ADR-0034
established that dimensions below the agreement floor cannot fail a build. A
renderer that folded them in would draw a subject as regressing on a dimension
declared unable to gate — the same mistake as gating on it, made one layer
later. Advisory failures get their own column.

**No silent caps.** If a subject has more runs than the display limit, the
page says how many were dropped. A view showing the last N looks identical to
one showing everything, and the difference matters exactly when someone is
chasing a regression.

## What it found on its first run

Two findings, from 54 records across 13 subjects. The records that produced
them were discarded with the git design, so both need re-establishing from
regenerated runs — deliberately, because neither had been written down
anywhere else:

- **`tool-selection`** climbed 47% → 93% → 93% → 100% over four runs on
  `claude-sonnet-5` (RC1-249, iterating on tool descriptions), then dropped to
  **86% on `claude-haiku-4-5`** — attributed to the model change.
- **`groundedness`** showed the harness pin bump `code: 0.1.0 → 0.2.0` with
  **no score change**, reproducing automatically what RC1-261 verified by
  hand. The reassuring one: a pin bump that moves a score is itself the
  finding, and this is the view that would show it.
