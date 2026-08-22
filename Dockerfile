# creative-agent runtime image.
#
# Three stages so the same Dockerfile serves CI and deployment:
#   base    installs the locked dependencies (cached across source edits)
#   test    adds the suite and runs the gate — `make docker-test`
#   runtime a slim, non-root image whose entrypoint is the CLI
#
# The image ships no credentials: ANTHROPIC_API_KEY is supplied at run time, and
# `--offline` runs the deterministic checks with no key at all.

ARG PYTHON_VERSION=3.11

# --- base --------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# Dependency layer first: source edits do not invalidate the resolved environment.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-install-project --no-dev

COPY src ./src
RUN uv sync --locked --no-dev

# --- test --------------------------------------------------------------------
FROM base AS test

RUN uv sync --locked --all-extras
COPY tests ./tests
COPY scripts ./scripts
COPY Makefile ./
# Default command runs the same gate CI runs, so a green container means a green CI.
CMD ["sh", "-c", "uv run ruff check . && uv run mypy && uv run pytest && \
     uv run python scripts/check_coverage_floors.py coverage.xml pyproject.toml"]

# --- runtime -----------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    # Structured output by default in a container: logs are collected, not read.
    CREATIVE_AGENT_LOG_FORMAT=json \
    CREATIVE_AGENT_LOG_LEVEL=INFO

# Reviews read untrusted documents, so run unprivileged.
RUN useradd --create-home --uid 10001 reviewer

COPY --from=base /opt/venv /opt/venv
COPY --from=base /app/src /app/src

WORKDIR /home/reviewer
USER reviewer

# Review state is durable and belongs on a mount, not in the layer.
VOLUME ["/home/reviewer/review-log"]
ENV CREATIVE_AGENT_REVIEW_LOG_DIR=/home/reviewer/review-log

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD ["creative-agent", "oracles", "validate", "--all"]

ENTRYPOINT ["creative-agent"]
CMD ["--help"]
