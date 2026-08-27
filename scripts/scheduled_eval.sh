#!/usr/bin/env zsh
# Scheduled eval runs from the dev machine (RC1-315): `daily` runs the free
# kpi-ledger suite, `weekly` sweeps every suite in docs/measuring.md. Both
# finish by publishing the trend page. launchd invokes this via the plists in
# launchd/ — see scripts/install_launchd.sh.
#
# The credential story is unchanged: EVAL_DATABASE_URL is read from ~/.zshrc,
# its one home, and this script runs on the same developer machine manual runs
# use — "never CI" was about where the credential lives, not about scheduling
# (docs/trend.md, "Scheduled runs: an amendment, not a reversal").
#
# A suite exiting 1 (cases failed) or 2 (cases errored) is a finding, not a
# wrapper failure: the run is recorded, the sweep continues, and the page
# publishes either way — a scheduled run that hides a bad score defeats the
# point of scheduling it. Suites needing an exported ANTHROPIC_API_KEY are
# skipped with a logged notice when the key is absent, never silently.
set -u
setopt pipefail

source ~/.zshrc 2>/dev/null || true
# launchd starts with a minimal PATH; uv and homebrew tools live off it.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

PERSONAL="$HOME/programming/personal"
EVALS_REPO="$PERSONAL/agent-evals"
MODE="${1:-}"

log() { print -- "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

if [[ "$MODE" != "daily" && "$MODE" != "weekly" ]]; then
  log "usage: scheduled_eval.sh daily|weekly"; exit 64
fi
if [[ -z "${EVAL_DATABASE_URL:-}" ]]; then
  # Without the store, suites fall back to local JSONL — a forked history the
  # schedule would silently grow. Refuse instead.
  log "EVAL_DATABASE_URL is not set after sourcing ~/.zshrc — refusing to run"; exit 78
fi

failures=0
suite() {  # suite <label> <repo-dir> <command...>
  local label="$1" dir="$PERSONAL/$2"; shift 2
  log "-- $label: $*"
  ( cd "$dir" && "$@" )
  local rc=$?
  case $rc in
    0) log "   $label: all cases passed" ;;
    1) log "   $label: cases FAILED (recorded — read the page)" ;;
    2) log "   $label: cases ERRORED (recorded — subject produced nothing to score)" ;;
    *) log "   $label: suite did not run (exit $rc)"; (( failures++ )) ;;
  esac
}

log "== scheduled $MODE run"

if [[ "$MODE" == "daily" ]]; then
  suite kpi-ledger tpm-automation-platform .venv/bin/python -m evals run kpi-ledger
else
  # The full sweep: every suite in docs/measuring.md, free halves included.
  for s in tool-selection status-narrative work-breakdown dependency raid spec-review \
           groundedness status-narrative-fallback health spec-structural; do
    suite "$s" launch-planner-agent uv run evals run "$s"
  done
  for s in drift-digest drift-digest-allclear kpi-ledger; do
    suite "$s" tpm-automation-platform .venv/bin/python -m evals run "$s"
  done
  suite pr-review pr-request-agent .venv/bin/python -m evals
  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    suite stakeholder-status-email n8n-stakeholder-status-email .venv/bin/python -m evals
    suite concert-preview n8n-concert-intelligence .venv/bin/python -m evals
    suite incident-summary ai_powered_incident_alert_summarizer .venv/bin/python -m evals
  else
    log "-- SKIPPED stakeholder-status-email, concert-preview, incident-summary:"
    log "   their suites read an exported ANTHROPIC_API_KEY and none is set (docs/measuring.md)"
  fi
fi

log "-- publishing trend page"
if "$EVALS_REPO/scripts/publish_trend.sh"; then
  log "== $MODE run done ($failures suite(s) failed to run)"
else
  log "== $MODE run done but PUBLISH FAILED ($failures suite(s) also failed to run)"
  exit 1
fi
(( failures == 0 ))
