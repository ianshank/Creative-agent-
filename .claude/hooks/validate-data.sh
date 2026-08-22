#!/usr/bin/env bash
# PostToolUse hook: re-validate the data that silently breaks things.
#
# Two classes of file are executable configuration rather than code: the oracle doctrine
# tables (the product itself) and the .claude assets (agents, skills, hooks, settings).
# A schema break in either fails at the worst moment — mid-review, in someone else's
# session — so it is caught at edit time. Reads the hook payload from stdin; exit 2
# feeds the error back to Claude to fix immediately.
set -euo pipefail

payload="$(cat)"
cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"

case "$payload" in
  *data/oracles/*)
    if ! output="$(uv run creative-agent oracles validate --all 2>&1)"; then
      echo "Oracle validation failed after this edit:" >&2
      echo "$output" >&2
      exit 2
    fi
    ;;
esac

case "$payload" in
  *.claude/agents/*|*.claude/skills/*|*.claude/hooks/*|*.claude/settings.json)
    if ! output="$(uv run creative-agent assets validate 2>&1)"; then
      echo "Asset validation failed after this edit:" >&2
      echo "$output" >&2
      exit 2
    fi
    ;;
esac

exit 0
