#!/usr/bin/env bash
# Render the trend page from the eval store and publish it to GitHub Pages.
#
# Runs entirely from a developer machine — CI holds no database credential
# (RC1-263). The page goes up as a single parentless commit force-pushed to
# `gh-pages`, so the rendered artifact never appears in main's history, never
# shows up in a diff, and the branch never grows.
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
git push -f origin "$commit:refs/heads/gh-pages"
echo "published $commit to gh-pages"
