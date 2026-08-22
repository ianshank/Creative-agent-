# CLAUDE.md

## Commands

- Install: `uv sync --all-extras` (or `pip install -e ".[dev,llm]"`)
- Test: `uv run pytest` (coverage gate: branch, 90% global; live tests excluded by default)
- Lint/format: `uv run ruff check .` / `uv run ruff format .`
- Types: `uv run mypy`
- Layering: `uv run lint-imports` — `harness/` and `models/` must never import `agents/`
- Oracle check: `uv run creative-agent oracles validate --all`

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
  (e.g. D2 author list). Engine behavior tests use synthetic mini-oracles in
  `tests/fixtures/oracles/`.

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
- `.claude/skills/oracle-rebaseline/` — procedure for re-verifying doctrine sources
  (DOI/arXiv resolution + author-list diff).
