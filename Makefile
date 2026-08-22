# creative-agent developer entrypoints.
#
# One place where the quality gates are named, so a contributor, the Makefile, CI, and
# the review-gate skill cannot drift apart: CI calls these targets rather than repeating
# the commands. `make help` lists everything.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

UV ?= uv
RUN := $(UV) run
PYTHON_VERSIONS ?= 3.11 3.12 3.13
IMAGE ?= creative-agent
TAG ?= dev
# Overridable so a review can be run against any artifact without editing this file.
ARTIFACT ?= docs/architecture.md
ORACLE ?= sutton

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# --- environment -------------------------------------------------------------

.PHONY: install
install: ## Sync the locked dev environment
	$(UV) sync --all-extras --locked

.PHONY: install-unlocked
install-unlocked: ## Sync allowing dependency resolution to move (canary)
	$(UV) sync --all-extras --upgrade

# --- quality gates (CI calls these) ------------------------------------------

.PHONY: lint
lint: ## ruff check + format check
	$(RUN) ruff check .
	$(RUN) ruff format --check .

.PHONY: format
format: ## Apply ruff fixes and formatting
	$(RUN) ruff check . --fix
	$(RUN) ruff format .

.PHONY: types
types: ## mypy --strict over src
	$(RUN) mypy

.PHONY: layering
layering: ## import-linter contracts (harness/models must not import agents)
	$(RUN) lint-imports

.PHONY: oracles
oracles: ## Validate every oracle data file
	$(RUN) creative-agent oracles validate --all

.PHONY: assets
assets: ## Validate Claude Code agents, skills, hooks and settings
	$(RUN) creative-agent assets validate

.PHONY: test
test: ## Full suite with branch coverage gate
	$(RUN) pytest

.PHONY: coverage-floors
coverage-floors: ## Per-package and per-module coverage floors
	$(RUN) python scripts/check_coverage_floors.py coverage.xml pyproject.toml

.PHONY: secrets
secrets: ## gitleaks secret scan (no-op with a warning if gitleaks is absent)
	@if command -v gitleaks >/dev/null 2>&1; then \
		gitleaks detect --config .gitleaks.toml --redact --verbose; \
	else \
		echo "gitleaks not installed; skipping (CI runs it). See https://github.com/gitleaks/gitleaks"; \
	fi

.PHONY: gate
gate: lint types layering oracles assets test coverage-floors ## Everything CI runs, in fail-fast order
	@echo "gate: all checks passed"

# --- deeper, slower checks ---------------------------------------------------

.PHONY: mutation
mutation: ## Mutation testing over the enforcement core (slow, advisory)
	$(RUN) mutmut run --max-children 4 || true
	$(RUN) mutmut results

.PHONY: live
live: ## Live Claude Agent SDK tests (requires ANTHROPIC_API_KEY)
	$(RUN) pytest -m live --no-cov

.PHONY: goldens
goldens: ## Regenerate golden files (say so in the commit message)
	$(RUN) pytest --update-goldens

# --- running the product -----------------------------------------------------

.PHONY: review-offline
review-offline: ## Deterministic-only review of $(ARTIFACT); no API key needed
	$(RUN) creative-agent --verbose review $(ARTIFACT) --oracle $(ORACLE) --offline

.PHONY: review
review: ## Full review of $(ARTIFACT) (requires ANTHROPIC_API_KEY)
	$(RUN) creative-agent --verbose review $(ARTIFACT) --oracle $(ORACLE)

.PHONY: rebaseline
rebaseline: ## Re-resolve $(ORACLE) citations against arXiv (dry run)
	$(RUN) creative-agent oracles rebaseline $(ORACLE) --dry-run

# --- container ---------------------------------------------------------------

.PHONY: docker-build
docker-build: ## Build the runtime image
	docker build -t $(IMAGE):$(TAG) .

.PHONY: docker-test
docker-test: ## Run the suite inside the image
	docker build --target test -t $(IMAGE):test . && docker run --rm $(IMAGE):test

.PHONY: docker-review
docker-review: ## Offline review of $(ARTIFACT) inside the image
	docker run --rm -v "$(PWD):/work:ro" $(IMAGE):$(TAG) \
		review /work/$(ARTIFACT) --oracle $(ORACLE) --offline

# --- housekeeping ------------------------------------------------------------

.PHONY: clean
clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis htmlcov mutants \
		coverage.xml .coverage .coverage.* dist build
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
