---
name: inspect-state
description: >
  Inspect what the creative-agent harness currently knows — registered agents, oracle
  data, decision-log status, a specific artifact's review history, and Claude Code asset
  health — without running a review. Use before a review to sanity-check setup, when a
  review's mode or exit code looks wrong, when auditing which agents/oracles are
  registered, or when debugging an asset-validation failure.
---

# Inspecting harness state

These are read-only introspection commands. None of them run an LLM call or write state;
reach for them before or between reviews, not as part of the review-gate or rebaseline
workflows (see those skills for those).

## What's registered

```bash
uv run creative-agent oracles list        # every oracle_id the loader can find, with row counts
uv run creative-agent agents list         # every ReviewAgent registered in AgentRegistry
```

Use these first when a `review --oracle <id>` or `--agent <name>` fails with an unknown-name
error — they show exactly what the CLI can currently resolve, including oracles added via
`CREATIVE_AGENT_ORACLE_SEARCH_PATHS` for local-only corpora (see the `add-oracle` skill).

## Decision-log status

```bash
uv run creative-agent decisions check --repo <path> --oracle <oracle_id>
```

Checks a reviewed artifact's own `docs/decision-log.md` for the required decisions the
oracle names (e.g. sutton's `DEC-S1`..`DEC-S6`) — this is `DecisionGate`, and it is
**only** meaningful with `--repo` pointed at the artifact's repository, never this one:
`docs/decision-log.md` in *this* repo holds framework decisions (`DEC-F*`), which are a
different thing entirely.

## A specific artifact's review history

```bash
uv run creative-agent state show <artifact-id>
```

Prints the cycle count, findings, and disposition history from
`docs/review-log/<artifact-id>.md`. Use this to see whether a Blocker is `open` (still
outstanding), `disputed`, or `waived` before deciding whether a repeat review should
escalate — the same history `CycleEscalator` reads to decide a cycle-3 charter-review STOP.
An artifact that has never been reviewed prints `cycles: 0`, not an error.

## Asset health

```bash
uv run creative-agent assets validate [--claude-dir PATH]
```

Runs the same schema and behavioural checks the `PostToolUse` hook runs automatically on
every edit under `.claude/` — call it directly when debugging a defect the hook already
reported, or before hand-authoring a new agent/skill, to see every defect at once instead
of one edit at a time. Defaults to this repository's own `.claude/`; pass `--claude-dir` to
validate a different Claude Code project's assets using this same validator.

## Review flags that change behavior, not just output

- `review --mode auto|conformance|advisory` — overrides the fail-closed mode resolution
  (DEC-F6) for one run. `auto` (the default) infers mode from whether the artifact quotes a
  conformance marker; forcing `conformance` on an artifact that never claims the program is
  how to deliberately hold it to the full doctrine bar anyway.
- `review --output-json PATH` — writes the structured `ReviewReport` alongside the
  rendered markdown, for scripting against a review's result rather than parsing prose.
- `--log-format text|json` (global, before the subcommand) — `json` is what a log
  aggregator wants; `text` is what a human reading a terminal wants. Independent of
  `--verbose`/`--debug`, which raise the log *level*, not the format.
