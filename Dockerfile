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
# `--extra llm` is load-bearing, not belt and braces. `claude-agent-sdk` is an optional
# extra and there is no `default-extras`, so a plain `uv sync` produced a runtime image
# with no SDK at all: every review that was not `--offline` failed with LLMTransportError,
# in an image whose own header says an API key is supplied at run time (DEC-F36).
#
# `--no-dev` is deliberately absent: `dev` here is an *extra*, not a PEP 735 dependency
# group, so the flag referred to a group that does not exist and excluded nothing. A no-op
# flag that reads as protection is worse than no flag.
RUN uv sync --locked --no-install-project --extra llm

COPY src ./src
RUN uv sync --locked --extra llm

# --- test --------------------------------------------------------------------
FROM base AS test

# make is not in python:slim; the gate is defined once, in the Makefile, and the
# container must call that definition rather than restate it.
RUN apt-get update \
    && apt-get install --no-install-recommends -y make \
    && rm -rf /var/lib/apt/lists/*

RUN uv sync --locked --all-extras
COPY tests ./tests
COPY scripts ./scripts
COPY docs ./docs
COPY .claude ./.claude
COPY .github ./.github
# The suite audits the project's own configuration — that `make gate` still covers
# every CI check, that the ignore files protect what they claim, that the shipped
# Claude Code assets are valid. Those files are inputs to the tests, so a test stage
# without them passes vacuously and the "green container means green CI" claim breaks.
COPY Makefile CHANGELOG.md CLAUDE.md Dockerfile .gitignore .dockerignore .gitleaks.toml ./
# One definition of the gate, called here rather than restated: a green container is a
# green CI because both run the same target.
CMD ["make", "gate"]

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
