#!/usr/bin/env bash
# PostToolUse hook: re-validate oracle data after any edit under data/oracles.
#
# The doctrine table IS the product. A schema break there fails CI and every review, so
# catching it at edit time is worth the round trip. Reads the hook payload from stdin;
# exits 0 unless an oracle file was touched and now fails validation, in which case
# exit 2 feeds the error back to Claude to fix immediately.
set -euo pipefail

payload="$(cat)"
case "$payload" in
  *data/oracles/*) ;;
  *) exit 0 ;;
esac

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"

if ! output="$(uv run creative-agent oracles validate --all 2>&1)"; then
  echo "Oracle validation failed after this edit:" >&2
  echo "$output" >&2
  exit 2
fi
exit 0
