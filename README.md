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
uv sync --all-extras           # or: pip install -e ".[dev,llm]"
creative-agent oracles validate --all
creative-agent review path/to/design.md --oracle sutton --offline   # no API key needed
export ANTHROPIC_API_KEY=...   # required for live reviews
creative-agent review path/to/design.md --oracle sutton
```

Exit codes: `0` clean/Info-only · `1` findings ≥ Major · `2` Blocker or charter-review STOP ·
`3` review failed (incomplete verification log) · `4` config/oracle error · `5` unexpected error.

## Architecture

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
`CREATIVE_AGENT_ORACLE_SEARCH_PATHS` at a directory — packaged data is the fallback.

## Adding a new agent

Implement the `ReviewAgent` protocol (`harness/protocols.py`), register it in
`agents/__init__.py` via `AgentRegistry.register()`, and ship its prompt templates. See
`agents/sutton_review/` for the reference implementation.

## Development

```bash
uv sync --all-extras
uv run ruff check . && uv run mypy && uv run pytest        # lint, types, tests + coverage gate
uv run pytest -m live                                       # live SDK tests (needs API key)
uv run lint-imports                                         # layering contract
```

State and audit artifacts land in `docs/review-log/`; framework decisions are logged in
`docs/decision-log.md` (CONFIRM-FIRST discipline — mechanisms are built only after their
governing decision is logged).
