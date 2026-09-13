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
#
# Every run ends by reporting itself to Datadog (RC1-415): one gauge saying
# whether the page published and one counting suites that failed to run,
# tagged with the mode. A monitor in tpm-automation-platform/datadog/ watches
# the first and goes red on a 0 or on a morning with no point at all — a
# failed publish, or a job that never started, is then as visible as any other
# failure on the estate instead of a line in a log nobody reads. DD_API_KEY
# keeps its one home in ~/.zshrc; without it the run logs that it went
# unreported, which the no-data side of the monitor will also notice.
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

report() {  # report <publish_ok 0|1> <suites_failed>
  if [[ -z "${DD_API_KEY:-}" ]]; then
    log "-- not reported to Datadog: DD_API_KEY is not set"; return
  fi
  local now site body
  now=$(date +%s); site="${DD_SITE:-datadoghq.com}"
  body=$(printf '{"series":[%s,%s]}' \
    "$(series agent_evals.scheduled_run.publish_ok "$1" "$now")" \
    "$(series agent_evals.scheduled_run.suites_failed "$2" "$now")")
  if curl -sS --fail -m 20 -o /dev/null -X POST "https://api.$site/api/v2/series" \
       -H "DD-API-KEY: $DD_API_KEY" -H 'Content-Type: application/json' -d "$body"; then
    log "-- reported to Datadog: publish_ok=$1 suites_failed=$2 mode=$MODE"
  else
    log "-- Datadog report FAILED (publish_ok=$1 suites_failed=$2 mode=$MODE)"
  fi
}
series() {  # series <metric> <value> <unix-ts>  — one v2 gauge point
  printf '{"metric":"%s","type":3,"points":[{"timestamp":%s,"value":%s}],"tags":["mode:%s"]}' \
    "$1" "$3" "$2" "$MODE"
}

log "-- publishing trend page"
if "$EVALS_REPO/scripts/publish_trend.sh"; then
  report 1 "$failures"
  log "== $MODE run done ($failures suite(s) failed to run)"
else
  report 0 "$failures"
  log "== $MODE run done but PUBLISH FAILED ($failures suite(s) also failed to run)"
  exit 1
fi
(( failures == 0 ))
