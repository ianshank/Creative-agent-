#!/usr/bin/env bash
# SessionStart hook: make a fresh checkout immediately able to run the suite.
#
# Claude Code on the web starts from a clean container, so without this every session
# begins by discovering that dependencies are missing. Quiet on success and fast on
# re-entry — `uv sync` is a no-op once the venv matches the lockfile. Never fails the
# session: a broken bootstrap should surface as a message, not block the work.
set -euo pipefail

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found; install from https://docs.astral.sh/uv/ to run the test suite" >&2
  exit 0
fi

if ! uv sync --all-extras --quiet 2>/dev/null; then
  echo "uv sync failed; the test suite may not run in this session" >&2
  exit 0
fi

echo "creative-agent ready — uv run pytest | uv run ruff check . | uv run mypy | uv run lint-imports"
