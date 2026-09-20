#!/usr/bin/env sh
# context-gate.sh — spawn the contexIT wiki refresh only when source actually changed.
#
# Port of openwiki-cc/hooks/openwiki-gate.sh (upstream: langchain-ai/openwiki v0.5.0).

set -eu

[ -n "${CONTEXTIT_HOOK:-}" ] && exit 0   # child run: never re-trigger

git rev-parse --git-dir >/dev/null 2>&1 || exit 0   # not a git repo

CONTEXT_DIR="${CONTEXT_DIR:-.context}"
meta="${CONTEXT_DIR}/.last-update.json"

head=$(git rev-parse HEAD 2>/dev/null) || exit 0

last=$([ -f "$meta" ] && sed -n 's/.*"gitHead":[[:space:]]*"\([^"]*\)".*/\1/p' "$meta" | head -1 || true)

dirty=$(git status --short --untracked-files=all \
  | grep -v "${CONTEXT_DIR}/.last-update.json$" \
  | grep -v ".contextit-run.json$" \
  || true)

if [ -n "$last" ] && [ -z "$dirty" ]; then
    [ "$head" = "$last" ] && exit 0
    outside=$(git diff --name-only "${last}..${head}" 2>/dev/null \
      | grep -v "^${CONTEXT_DIR}/" || true)
    [ -z "$outside" ] && exit 0
fi

CONTEXTIT_HOOK=1 setsid claude -p '/contextit:wiki update' --permission-mode acceptEdits >/dev/null 2>&1 &
