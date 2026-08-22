# CLAUDE.md

## Commands

Prefer the Makefile: it is the single definition of the gates, and CI calls the same
targets, so anything green locally should be green in CI.

- `make install` — locked dev environment
- `make gate` — lint, types, layering, oracle + asset validation, tests, coverage floors
- `make format` — apply ruff fixes and formatting
- `make test` / `make live` / `make mutation` / `make secrets`
- `make docker-test` — run the gate inside the container
- `make help` — every target

Underlying commands, if you need one in isolation: `uv run pytest`,
`uv run ruff check .`, `uv run mypy`, `uv run lint-imports`,
`uv run creative-agent oracles validate --all`, `uv run creative-agent assets validate`.

## Non-negotiable conventions

- **Doctrine tables are data.** Never hard-code doctrine rows, severity caps, gate lists,
  conformance markers, or thresholds in Python — they belong in `data/oracles/*.yaml`
  (schema: `models/oracle.py`). Model IDs, paths, and budgets come from `HarnessSettings`.
- **Determinism:** all time via the injected `Clock` protocol; `datetime.now`/`utcnow`/
  `date.today` are lint-banned in `src/` (ruff TID251). Identity hashing uses `hashlib`,
  never builtin `hash()`.
- **Decisions first:** structural changes require an entry in `docs/decision-log.md`
  (DEC-F series) before implementation. New coverage omits/pragmas also require one.
- **Versioned formats:** oracle YAML, state front matter, report contract, and LLM call
  schemas all carry versions; changing one needs a migration path and a frozen old-version
  test fixture.
- The shipped `sutton.v2.yaml` is used in tests only for validation + named invariants
  (e.g. the D2 author list). Engine behavior tests build synthetic oracles with
  `tests/factories.py::make_oracle` so product-data churn cannot break them.
- **Observability:** log through `harness/logging.py` (`get_logger`, `log_event`,
  `timed_stage`) with stable event names — never `print`. Never log prompts or artifact
  text; log sizes, ids, counts, and durations.
- **Assets are data too:** changes under `.claude/` must keep `make assets` green —
  agent filenames match their `name`, skills have trigger-worthy descriptions, hooks stay
  executable and use `set -e`.
- **Record the change:** user-visible changes get a `CHANGELOG.md` entry under
  Unreleased; deferred work goes in `docs/roadmap.md` rather than a TODO comment.

## Reviewing artifacts in other repos (worktree workflow)

Check the target repo out as a worktree (or clone), then:

```bash
creative-agent review /path/to/worktree/docs/design.md --oracle sutton \
    --artifact-repo /path/to/worktree
```

Review state and audit bundles stay in **this** repo under `docs/review-log/`. The
`--artifact-repo` flag additionally lets `DecisionGate` check the artifact repo's own
`docs/decision-log.md` for DEC-S1..S6 when the artifact claims Alberta-Plan/OaK conformance.

## Claude Code assets

- `.claude/agents/sutton-review.md` — subagent that delegates to the CLI and relays the
  rendered report unmodified.
- `.claude/skills/add-oracle/` — add a review corpus (data only, no code).
- `.claude/skills/review-gate/` — run and interpret the full local quality gate.
- `.claude/skills/oracle-rebaseline/` — re-verify doctrine sources (DOI/arXiv resolution
  + mechanical author-list diff).
- `.claude/hooks/session-start.sh` — SessionStart: `uv sync` so a fresh container can run
  the suite immediately.
- `.claude/hooks/validate-data.sh` — PostToolUse: re-validates oracle data after any
  edit under `data/oracles`, blocking on a schema break (exit 2) so it surfaces at edit
  time rather than in CI.

## Recurring work (loops)

Long-running checks are better as an interval than a habit:

```
/loop 30m run the review-gate skill and fix anything it reports
/loop 1h  check PR CI status and address failures
```

Scheduled equivalents already run in CI: the weekly live-SDK verification
(`.github/workflows/live.yml`) and weekly mutation testing
(`.github/workflows/mutation.yml`).
