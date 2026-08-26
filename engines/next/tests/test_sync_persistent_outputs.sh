#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

REMOTE="$TMP/remote.git"
SEED="$TMP/seed"
WORKER="$TMP/worker"
WRITER="$TMP/writer"

git init --bare "$REMOTE" >/dev/null
git clone "$REMOTE" "$SEED" >/dev/null 2>&1
(
  cd "$SEED"
  git config user.email test@example.com
  git config user.name test
  printf 'base\n' > code.txt
  printf 'generated-base\n' > generated.txt
  git add code.txt generated.txt
  git commit -m base >/dev/null
  git branch -M main
  git push -u origin main >/dev/null 2>&1
)
git --git-dir="$REMOTE" symbolic-ref HEAD refs/heads/main

git clone "$REMOTE" "$WORKER" >/dev/null 2>&1
git clone "$REMOTE" "$WRITER" >/dev/null 2>&1

(
  cd "$WORKER"
  git config user.email worker@example.com
  git config user.name worker
  printf 'canonical-output\n' > canonical.txt
  git add canonical.txt
  git commit -m 'canonical output' >/dev/null
  printf 'generated-after-scan\n' > generated.txt
  printf 'untracked-chart\n' > chart.tmp
)

(
  cd "$WRITER"
  git config user.email writer@example.com
  git config user.name writer
  printf 'remote-advance\n' >> code.txt
  git add code.txt
  git commit -m 'remote advance' >/dev/null
  git push origin main >/dev/null 2>&1
)

(
  cd "$WORKER"
  GITHUB_RUN_ID=123 GITHUB_RUN_ATTEMPT=1 bash "$ROOT/scripts/sync_persistent_outputs.sh" origin main 3
  test "$(cat generated.txt)" = 'generated-after-scan'
  test "$(cat chart.tmp)" = 'untracked-chart'
  test -z "$(git stash list)"
  test -z "$(git status --porcelain | grep -v -E '^( M generated.txt|\?\? chart.tmp)$' || true)"
)

git clone "$REMOTE" "$TMP/verify" >/dev/null 2>&1
test "$(cat "$TMP/verify/canonical.txt")" = 'canonical-output'
grep -q 'remote-advance' "$TMP/verify/code.txt"

echo 'sync_persistent_outputs dirty-worktree regression test passed'
