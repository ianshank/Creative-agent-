---
name: add-oracle
description: >
  Add a new review oracle (a different research corpus or review doctrine) to the
  creative-agent harness. Use when asked to review against a corpus other than
  sutton — "add an oracle for X", "review against the Y doctrine", "support a second
  research program" — or when adapting the harness to a new domain's review rules.
---

# Adding an oracle

An oracle is **data**, not code. The framework's central claim is that a new corpus is a
new YAML file with zero code changes — `tests/integration/test_cli_review.py::
TestSecondOracleNeedsNoCode` proves it. If you find yourself editing Python to support a
corpus, that is a bug in the harness, not a step in this procedure: fix the harness so
the behavior comes from `models/oracle.py` fields instead.

## Procedure

1. Copy `src/creative_agent/data/oracles/sutton.v2.yaml` to a new file and change
   `oracle_id`, `name`, `version`, `description`. Put it in `./data/oracles/` for a
   local-only oracle, or alongside the packaged one to ship it.
2. Replace the corpus-specific blocks, all of which are schema-validated:
   - `rows`: the doctrine table. Each row needs `id` (matching `^[A-Z]\d+[a-z]?$`),
     `principle`, `tier`, `check`, `failure_mode`, and `sources` with real identifiers.
     Rows with no in-corpus evidence set `disclosed_gap: true` and `tier: NONE`.
   - `conformance.markers`: phrases that indicate an artifact claims this program.
     Without a marker hit the review runs advisory and severities cap at
     `advisory_severity_cap`.
   - `gate_policy.gates`: the measurement gates this corpus demands, with
     `blueprint_missing_severity` on any gate whose absence is a Blocker for a class.
   - `artifact_classes`: what kinds of documents exist, which gates and sections each
     requires, and `missing_section_support` (which blocker basis a missing section
     establishes — not every corpus's required section is a safety property).
   - `severity_policy`: `tier_caps` (which tiers cannot carry a solo Blocker),
     `blocker_tiers`, `blocker_requires_any_of`, `unverified_row_cap`.
   - `source_quality`, `attribution.impersonation_patterns`,
     `decision_log_grammar`, `consistency.prose_symbols`, `protocol`.
3. Validate before running a review — the PostToolUse hook does this automatically, but
   run it explicitly when authoring:
   ```bash
   uv run creative-agent oracles validate <oracle_id>
   ```
   Cross-field errors name the offending key: unknown gate refs, duplicate row ids, a
   decision trap with no matching required decision, a placeholder row id that collides
   with the row grammar.
4. Resolve the citations rather than transcribing them — see the `oracle-rebaseline`
   skill. Transcription without resolution is the exact defect class the harness exists
   to catch.
5. Smoke-test offline (no API key needed), then live:
   ```bash
   uv run creative-agent review path/to/sample.md --oracle <oracle_id> --offline
   ```
6. If the corpus needs different prompt wording, add
   `data/prompts/<agent-template-dir>/<call-kind>.md.j2`; unspecified templates fall back
   to `data/prompts/default/`. Only add an agent class if the corpus needs different
   *prompting*, not merely different doctrine.
