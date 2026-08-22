---
name: review-gate
description: >
  Run the full local quality gate for creative-agent — lint, format, types, layering,
  full test suite with branch coverage, per-module floors, and oracle validation — and
  fix what it reports. Use before pushing, before opening or updating a PR, when CI is
  red, or when asked to "check everything", "run the gate", or "make it green".
---

# The quality gate

Run the same checks CI runs, in the order that fails fastest. Everything here is also
enforced by `.github/workflows/ci.yml`, so a green local run should mean a green CI run.

```bash
uv sync --all-extras --locked
uv run ruff check .            # lint (includes security + datetime bans)
uv run ruff format --check .   # formatting
uv run mypy                    # strict types
uv run lint-imports            # layering: harness/models must not import agents
uv run creative-agent oracles validate --all
uv run pytest                  # suite + branch coverage gate (--cov-fail-under=90)
uv run python scripts/check_coverage_floors.py coverage.xml pyproject.toml
```

## Interpreting failures

- **Coverage floor failure** names the package or module and whether it was lines or
  branches. Add the missing test; do not lower the floor. Adding an entry to the
  coverage `omit` list fails the gate by design — it requires a `docs/decision-log.md`
  entry (DEC-F8) and an update to `APPROVED_OMITS`.
- **"no measured lines — the floor matched nothing"** means a path was renamed and the
  gate silently stopped covering it. Fix the prefix in `scripts/check_coverage_floors.py`.
- **`lint-imports` contract broken** means something in `harness/` or `models/` imported
  an agent plugin. Invert the dependency: the harness takes data or a protocol, the
  agent supplies it.
- **A banned `datetime.now`/`date.today`** means a module needs the injected `Clock`
  (DEC-F8) — determinism is what makes the golden tests and escalation logic testable.
- **Golden mismatch** is intentional only when the output contract changed. Re-generate
  with `uv run pytest --update-goldens` and say so in the commit message; otherwise it
  is a regression in the renderer or the prompt templates.

## Deeper checks (not in the per-PR gate)

```bash
uv run mutmut run           # surviving mutants in the enforcement core (weekly in CI)
uv run pytest -m live       # real Claude Agent SDK calls; needs ANTHROPIC_API_KEY
```
