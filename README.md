# creative-agent

An **agent harness framework** for doctrine-driven review agents, shipping with
**sutton-review** — a design-review agent that audits agent architectures, RL algorithm
claims, and embodied-deployment blueprints against the published Sutton/Alberta-Plan corpus.

The harness is a hybrid: a **deterministic, unit-testable core** enforces all structure
(doctrine loading, severity capping, verification-log completeness, tool honesty, review-state
persistence, cycle escalation, output contract), while the LLM — via the Claude Agent SDK —
performs only the design judgement itself.

## Quickstart

```bash
make install                   # uv sync --all-extras --locked
make gate                      # lint, types, layering, data, tests, coverage floors
make review-offline ARTIFACT=path/to/design.md    # no API key needed
export ANTHROPIC_API_KEY=...   # required for live reviews
make review ARTIFACT=path/to/design.md
```

`make help` lists every target. CI calls the same targets, so a green `make gate` should
mean a green pipeline. Without `make`, the underlying commands are plain `uv run`
invocations — see the [`review-gate` skill](.claude/skills/review-gate/SKILL.md).

In a container:

```bash
make docker-build
make docker-review ARTIFACT=docs/architecture.md
```

Exit codes: `0` clean/Info-only · `1` findings ≥ Major · `2` Blocker or charter-review STOP ·
`3` review failed (incomplete verification log) · `4` config/oracle error · `5` unexpected error.

Add `--verbose` (INFO) or `--debug` (every stage and LLM call, with durations), and
`--log-format json` to emit one JSON object per line for a log store:

```bash
creative-agent --debug --log-format json review design.md --offline
```

## Architecture

Full C4 views (context, container, component) and the review sequence are in
[`docs/architecture.md`](docs/architecture.md). The short version:

```
creative_agent/
├── harness/     # generic framework: pipeline, severity, gates, verification, state, security
├── models/      # pydantic v2 schemas — every durable format carries a schema_version
├── agents/      # plugins (sutton_review) — prompts and parsing only, no enforcement logic
└── data/        # oracle YAMLs and prompt templates (override via CREATIVE_AGENT_* settings)
```

Three rules keep it modular:

1. **Doctrine is data.** The entire review oracle — rows D1–D12, evidence tiers, severity
   caps, measurement gates, source-quality rules, conformance markers — lives in
   `data/oracles/sutton.v2.yaml`. A new research corpus is a new YAML file, not new code.
2. **The harness never imports agents** (enforced by import-linter). Agents supply prompt
   templates and a default oracle; enforcement lives in the harness.
3. **No hard-coded values.** Model IDs, paths, budgets, and thresholds come from
   `HarnessSettings` (env prefix `CREATIVE_AGENT_`), a config file, or oracle data.

## Adding a new oracle

Copy `src/creative_agent/data/oracles/sutton.v2.yaml`, change `oracle_id`, edit the rows, and
run `creative-agent oracles validate <name>`. Place overrides in `./data/oracles/` or point
`CREATIVE_AGENT_ORACLE_SEARCH_PATHS` at one or more directories — packaged data is the
fallback. List-valued settings accept `a,b`, `a:b`, or JSON:

```bash
CREATIVE_AGENT_ORACLE_SEARCH_PATHS=/srv/oracles:/etc/oracles
CREATIVE_AGENT_AGENT_TOOLS=Read,WebFetch
```

A second oracle needs **no code**: `tests/integration/test_cli_review.py::
TestSecondOracleNeedsNoCode` runs a review against a non-sutton corpus to prove it.

## Adding a new agent

Implement the `ReviewAgent` protocol (`harness/protocols.py`), register it in
`agents/__init__.py` via `AgentRegistry.register()`, and ship its prompt templates. See
`agents/sutton_review/` for the reference implementation.

## Development

```bash
make gate            # everything CI runs, fail-fast
make format          # apply ruff fixes and formatting
make test            # suite + branch coverage gate
make live            # live Claude Agent SDK tests (needs ANTHROPIC_API_KEY)
make mutation        # mutation testing over the enforcement core (slow, advisory)
make secrets         # gitleaks scan
make assets          # validate the Claude Code agents, skills and hooks
```

The `.claude` assets are executable configuration and are validated like any other data:
`creative-agent assets validate` checks agent and skill frontmatter, hook executability,
and that `settings.json` references scripts that exist. A PostToolUse hook runs it (and
oracle validation) automatically after edits to either.

State and audit artifacts land in `docs/review-log/`; framework decisions are logged in
`docs/decision-log.md` (CONFIRM-FIRST discipline — mechanisms are built only after their
governing decision is logged).

## Further reading

| Document | What it covers |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | C4 views, review sequence, extension points, trust boundaries |
| [`docs/decision-log.md`](docs/decision-log.md) | Framework decisions DEC-F1..F11 and their rationale |
| [`docs/roadmap.md`](docs/roadmap.md) | What is deliberately not built yet, and what unblocks it |
| [`CHANGELOG.md`](CHANGELOG.md) | Release history, including the durable-format versions |
| [`CLAUDE.md`](CLAUDE.md) | Conventions for working in this repo |
