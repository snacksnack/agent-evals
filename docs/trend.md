# The trend view, and why it is a static page (RC1-255)

**Decision.** Consumer repos commit their billed run records. A pure renderer in
`agent_evals/trend.py` turns them into one self-contained HTML page, published
to GitHub Pages from this repo. No service, no database, no credentials.

## Why not a service

The records are already an append-only log carrying the model, the prompt
version and the code version. Git already stores an append-only log with
history, blame and diff. Standing up a second one duplicates what the repo does
for free, and adds a moving part to babysit for data that changes a few times a
week.

`tpm-automation-platform` makes the same argument about its own scheduling —
n8n was dropped once the logic left it, because *"keeping a whole SaaS around
just to fire a daily HTTP request wasn't worth the moving part."*

## Why fetching is not the cross-repo dispatch this epic rejected

RC1-248 and RC1-255 both refused repository-dispatch machinery, on the grounds
that plumbing invented to work around a repo choice is a signal the choice is
wrong. That machinery needed tokens, write access, and coordination between
workflows.

This reads five public files over HTTPS. No token, no write access, no
coordination — a consumer repo does not know this page exists. That asymmetry is
the whole reason it is a workflow step rather than infrastructure.

## Only billed runs are committed

Billed suites are run by hand at decision points, so each record is a
measurement someone chose to take. Free CI runs are a **gate**: they answer
pass/fail on every push, and trending "the deterministic checker still returns
36/36" would bury the signal under noise. Having CI commit on every push would
also need write access in five workflows, for no gain.

So `eval-runs/runs.jsonl` is committed and the free subjects' records are not.
Each consumer repo's `.gitignore` says which is which.

## What the page claims, and what it does not

One question: **a score moved — what moved with it?** Every point carries its
three versions, and a row is marked where any of them changed since the previous
run. A drop beside a mark has a suspect. A drop with nothing marked means
something moved that the record does not carry, and inventing an attribution
there would be worse than showing none.

It does not do significance testing. With runs numbering in the dozens, it
should not pretend to.

**Advisory results never count against a pass rate.** ADR-0033 and ADR-0034
established that dimensions below the agreement floor cannot fail a build. A
renderer that folded them in would draw a subject as regressing on a dimension
declared unable to gate — the same mistake as gating on it, made one layer
later. Advisory failures get their own column.

**No silent caps.** If a subject has more runs than the display limit, the page
says how many were dropped. A view showing the last N looks identical to one
showing everything, and the difference matters exactly when someone is chasing a
regression.

## What it found on its first run

Two things, from 54 records across 13 subjects:

- **`tool-selection`** climbed 47% → 93% → 93% → 100% over four runs on
  `claude-sonnet-5` (RC1-249, iterating on tool descriptions), then dropped to
  **86% on `claude-haiku-4-5`** — attributed to the model change, and a finding
  nobody had written down.
- **`groundedness`** shows the harness pin bump `code: 0.1.0 → 0.2.0` with **no
  score change**, reproducing automatically what RC1-261 verified by hand.

The second is the more reassuring one: a pin bump that moves a score is itself
the finding, and this is the view that would show it.
