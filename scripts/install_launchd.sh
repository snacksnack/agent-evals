#!/usr/bin/env zsh
# Install (or reinstall) the scheduled eval launchd jobs (RC1-315).
# Substitutes __HOME__ in the templates, drops them in ~/Library/LaunchAgents,
# and bootstraps them for the logged-in user. Safe to re-run after editing a
# template or the schedule. Remove with:
#   launchctl bootout gui/$UID/com.hihelloreid.agent-evals.daily
#   launchctl bootout gui/$UID/com.hihelloreid.agent-evals.weekly
set -euo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"

mkdir -p ~/Library/LaunchAgents ~/Library/Logs/agent-evals
for name in daily weekly; do
  label="com.hihelloreid.agent-evals.$name"
  target=~/Library/LaunchAgents/$label.plist
  sed "s|__HOME__|$HOME|g" "launchd/$label.plist" > "$target"
  launchctl bootout "gui/$UID/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$UID" "$target"
  echo "installed $label"
done
launchctl list | grep com.hihelloreid.agent-evals
