#!/usr/bin/env sh
# cairn-gate.sh — spawn the Cairn wiki refresh only when source actually changed.
#
# Port of langchain-ai/openwiki v0.5.0).
#
# Host-agnostic: set CAIRN_AGENT to pick the headless runner (claude | codex | copilot).
# Wired automatically by hooks/hooks.json (Claude Code + Copilot CLI Stop hook) and
# .codex/hooks.json (Codex Stop hook).

set -eu

[ -n "${CAIRN_HOOK:-}" ] && exit 0   # child run: never re-trigger

git rev-parse --git-dir >/dev/null 2>&1 || exit 0   # not a git repo

CAIRN_DIR="${CAIRN_DIR:-.cairn}"
meta="${CAIRN_DIR}/.last-update.json"

head=$(git rev-parse HEAD 2>/dev/null) || exit 0

last=$([ -f "$meta" ] && sed -n 's/.*"gitHead":[[:space:]]*"\([^"]*\)".*/\1/p' "$meta" | head -1 || true)

dirty=$(git status --short --untracked-files=all \
  | grep -v "${CAIRN_DIR}/.last-update.json$" \
  | grep -v ".cairn-run.json$" \
  || true)

if [ -n "$last" ] && [ -z "$dirty" ]; then
    [ "$head" = "$last" ] && exit 0
    outside=$(git diff --name-only "${last}..${head}" 2>/dev/null \
      | grep -v "^${CAIRN_DIR}/" || true)
    [ -z "$outside" ] && exit 0
fi

case "${CAIRN_AGENT:-claude}" in
    codex)
        CAIRN_HOOK=1 setsid codex exec 'update the cairn wiki (.cairn/)' >/dev/null 2>&1 &
        ;;
    copilot)
        CAIRN_HOOK=1 setsid copilot -p 'update the cairn wiki (.cairn/)' --allow-tool='shell,write,read' >/dev/null 2>&1 &
        ;;
    *)
        CAIRN_HOOK=1 setsid claude -p '/cairn:wiki update' --permission-mode acceptEdits >/dev/null 2>&1 &
        ;;
esac
