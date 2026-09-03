---
name: live-verify
description: >
  Verify creative-agent against the real Claude Agent SDK — the live test leg plus a full
  end-to-end review of a planted document. Use before a release, after changing anything
  in harness/llm/, after changing the PreToolUse hook or tool scoping, when the weekly
  live workflow fails, and whenever a change is green in CI but has never been run against
  a real model. Also use when asked whether the harness actually works, or to reproduce a
  live failure.
---

# Verify against the real SDK

Every mocked test in this repository hands the model's answer over out of band:
`transport()` yields `ResultMessage` objects directly, so no `PreToolUse` hook ever runs.
That is a sound way to test message handling and a blind spot for everything about the
session itself. The blind spot has already cost one product-breaking defect: deny-by-default
denied `StructuredOutput`, the SDK's own response channel, so **every real review failed**
while 721 tests, a 95% coverage gate and a 470/470 mutation run were all green (DEC-F20).

There is no substitute for a real call. Run both legs below.

## 1. The live test leg

```bash
uv run pytest -m live --no-cov -q
```

Live tests are deselected from the default suite by `addopts = "-m 'not live'"`, so this
is the only way they run locally. They gate on a usable credential, not on
`ANTHROPIC_API_KEY` specifically: the SDK spawns the `claude` CLI and inherits its
session, so an authenticated CLI is enough and gating on the key alone made this leg skip
in environments where a live call works (DEC-F21).

**A skip is a failure of this task, not a pass.** If it reports "no usable Claude Agent
SDK credential", say so plainly rather than reporting the run as clean — that message is
how the defect above stayed hidden for a whole branch.

## 2. A full end-to-end review

The live leg exercises one adapter call. It does not exercise the pipeline: classify, the
per-row sweep, the deterministic checkers, the repair loop, rendering, and the state
write. Run a real review against a planted document:

```bash
cat > /tmp/live-probe.md <<'DOC'
---
review-id: live-probe
---
# Continual-learning agent design
## Approach
A deep network trained with Adam on a 1M-transition replay buffer, refreshed nightly.
Feature construction uses a pretrained encoder, frozen after the initial fit.
## Evaluation
We evaluate on a held-out week of logged traffic and expect a 30% improvement.
## Claimed conformance
This design follows the Alberta Plan and is fully continual.
DOC

CREATIVE_AGENT_LLM_TIMEOUT_SECONDS=300 CREATIVE_AGENT_MAX_BUDGET_USD=10 \
  uv run creative-agent review /tmp/live-probe.md --oracle sutton --mode advisory \
  --artifact-id live-probe
```

Budget it. A sutton review is roughly 18 provider calls on the happy path and takes
several minutes; `max_budget_usd` aborts with exit 6 rather than running away.

**What a passing run looks like** — check all of these, not just the exit code:

- A `**VERDICT**` line, a findings table, a doctrine-sweep row for every oracle row, and a
  verification log.
- Findings attributed to doctrine rows (`D1`, `D5`, …), not all to `—`.
- No `security.pretooluse_denied` for a tool the review needs. `ToolSearch` being denied is
  expected and harmless; `StructuredOutput` being denied is the DEC-F20 defect.
- Exit 0 under `--mode advisory` (everything caps at Info) or 1/2 under `--mode
  conformance`. Exit 5 is never acceptable — it means an untyped crash.

**Clean up afterwards.** The run writes `docs/review-log/live-probe.md`, a `live-probe/`
bundle and a lock file into the repository, and `tests/unit/test_project_config.py`
asserts only `.gitkeep` is tracked there:

```bash
rm -rf docs/review-log/live-probe.md docs/review-log/live-probe docs/review-log/.live-probe.lock
```

Never `git add -A` after a live run.

## Reading a failure

- `Failed to provide valid structured output after N attempts` with
  `security.pretooluse_denied ... tool_name='StructuredOutput'` → the response channel is
  denied. Check `HarnessSettings.protocol_tools` (DEC-F20).
- `ResultError` with no denial logged → the SDK surface has drifted from the mocked
  transport in `tests/unit/test_claude_sdk_adapter.py`. Update the mock to match reality;
  the mock is the copy, not the source.
- Exit 6 → budget or timeout, not a defect. Raise the knob and re-run.
- Exit 3 → the verification log could not be completed. That is a finding about the
  document, and for the probe above it is a plausible outcome, not a tool failure.

If a real call fails for a reason a mocked test could have caught, add the mocked test
too — the live leg is weekly and slow, and it should not be the only thing standing
between a defect and a user.
