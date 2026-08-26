#!/usr/bin/env bash
set -euo pipefail

remote="${1:-origin}"
branch="${2:-${GITHUB_REF_NAME:-main}}"
retries="${3:-3}"
stash_created=false

restore_generated_state() {
  if [ "$stash_created" != true ]; then
    return 0
  fi
  echo "Restoring generated working-tree state after sync"
  if ! git stash pop --quiet; then
    git status --short || true
    echo "::error::Unable to restore generated working-tree state after canonical sync."
    return 1
  fi
  stash_created=false
}

if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
  echo "Temporarily preserving generated files before canonical rebase"
  git status --short
  git stash push --include-untracked -m "stockscout-sync-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}"
  stash_created=true
fi

pushed=false
for attempt in $(seq 1 "$retries"); do
  echo "Sync attempt $attempt/$retries"
  git fetch "$remote" "$branch"
  if git rebase "$remote/$branch" && git push "$remote" "HEAD:$branch"; then
    pushed=true
    break
  fi
  git rebase --abort || true
  sleep $((attempt * 3))
done

if [ "$pushed" != true ]; then
  restore_generated_state || true
  echo "::error::Canonical push failed after retries; Pages deployment is blocked to prevent publishing data that is not persisted in Git."
  exit 1
fi

restore_generated_state

echo "Canonical outputs synced and generated working-tree state restored"
