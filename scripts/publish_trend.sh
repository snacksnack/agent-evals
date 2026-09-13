#!/usr/bin/env bash
# Render the trend page from the eval store and publish it to GitHub Pages.
#
# Runs entirely from a developer machine — CI holds no database credential
# (RC1-263). The page goes up as a single parentless commit force-pushed to
# `gh-pages`, so the rendered artifact never appears in main's history, never
# shows up in a diff, and the branch never grows.
#
# The remote decides whether the publish happened, not the push's exit status
# (RC1-415). On 2026-09-08 the push landed — GitHub's activity log and the
# Pages build both show gh-pages moving to the commit this script built — yet
# the client, eleven minutes later, reported "cannot lock ref … is at <that
# same commit>" and the scheduled run logged PUBLISH FAILED for a page that was
# current. So: cap how long a stalled transfer may hang, retry a rejected push
# once, and then read gh-pages back; equal to our commit is published,
# anything else is a failure, whatever git said on the way.
#
# Needs: EVAL_DATABASE_URL set, and Pages configured to serve `gh-pages`.
set -euo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"

uv run python -m agent_evals.trend_cli --out site/index.html

# Plumbing rather than a worktree: build a one-file tree and commit it with no
# parent, leaving the working tree and current branch untouched.
blob=$(git hash-object -w site/index.html)
tree=$(printf '100644 blob %s\tindex.html\n' "$blob" | git mktree)
commit=$(git commit-tree "$tree" -m "trend page")

remote_head() { git ls-remote origin refs/heads/gh-pages | cut -f1; }
push() {
  # Under 1 KB/s for 60 s is a stalled connection, not a slow one: fail it so
  # the retry and the read-back below get their turn instead of an 11-minute
  # hang.
  git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=60 \
    push -f origin "$commit:refs/heads/gh-pages"
}

if ! push; then
  if [[ "$(remote_head)" == "$commit" ]]; then
    echo "push reported a rejection but gh-pages is already at $commit"
  else
    echo "push rejected — retrying once"
    push || true
  fi
fi

head=$(remote_head)
if [[ "$head" != "$commit" ]]; then
  echo "PUBLISH FAILED: gh-pages is at ${head:-<unreadable>}, not $commit" >&2
  exit 1
fi
echo "published $commit to gh-pages"
