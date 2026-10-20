---
description: Run the eval suite, then render and publish the trend
---
Run the evals and publish the trend, in order, from the repo root:

1. Confirm `EVAL_DATABASE_URL` is set (it lives in `~/.zshrc`, never in CI). If it is missing, stop — without it a suite can't record to the run store.
2. `uv run --extra dev pytest` — the eval suite.
3. `scripts/publish_trend.sh` — render the trend page and push it to gh-pages.

Report the suite result (pass/fail counts) and confirm the trend was published. If the suite fails, stop before publishing and show the failures.
