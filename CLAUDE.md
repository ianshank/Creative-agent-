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

Two things `make gate` deliberately does not do, because both need a human to choose the
input:

- `uv run pytest -m live --no-cov` — the real SDK. Deselected by default; gated on a usable
  credential, which an authenticated `claude` CLI satisfies (no API key needed). A *skip* is
  not a pass. See `.claude/skills/live-verify`, and run a full end-to-end review too — the
  live leg exercises one adapter call, not the pipeline.
- `uv run python scripts/verify_guard.py --file <src> --find '<code>' --test '<selector>'` —
  proves a test fails when the code it guards is reverted. See below.

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
- **A test must fail when its subject is reverted.** Before claiming a test enforces
  anything, run `scripts/verify_guard.py` against it. This repository has shipped
  green-but-hollow tests at every level — a renderer check that grepped for an escape
  sequence, a glob parametrize listing only the shapes the code already caught, a budget
  test watching a value the neighbouring statement computes, a permission assertion re-reading
  its own fixture, an oracle invariant written to data that had just been hand-edited, and
  five one-line deletions in `pipeline.py`/`cli.py` that each left the whole suite passing.
  Coverage says a line executed; mutation testing says a mutant in the mutated set was
  killed. Neither says the assertion would have noticed. Write the leaf test *and* an
  integration assertion on the composed pipeline: the most common gap here is a well-tested
  control whose caller never opts in.
- **A gate must test the capability it needs, not a proxy for it.** The live leg skipped on
  `ANTHROPIC_API_KEY` for a backend that does not require one, so it reported "cannot check"
  in an environment where it works — and behind that silence sat a defect that broke every
  review (DEC-F20/F21). A gate that is wrong in the safe direction is worse than no gate,
  because it is counted.
- **Coverage floors are a ratchet.** Raise one whenever the suite clears it; that needs no
  ceremony. Lowering one needs a decision-log entry naming the coverage given up.

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
  + mechanical author-list diff). `verified: true` means *this command ran*; a careful hand
  reading goes in `notes` (DEC-F27).
- `.claude/skills/guard-check/` — prove a test fails when the code it guards is reverted.
- `.claude/skills/live-verify/` — the live test leg plus a real end-to-end review.
- `.claude/agents/gap-auditor.md` — read-only audit for hollow tests and for claims that
  outrun their code; reports findings marked PROVEN or REASONED.
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
