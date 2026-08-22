# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims at
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Durable formats carry their own version numbers independent of the package version —
oracle YAML `schema_version`, review-state `schema_version`, report `contract_version`,
and the LLM call contract — so a package release can be breaking for code while remaining
compatible for data, or the reverse. Changes to any of those are called out explicitly.

## [Unreleased]

Everything below is on `claude/implementation-plan-agent-framework-dbqgvv` and not yet on
`main`, which currently contains only the M0 bootstrap commit.

### Added

- **Agent harness framework** (`creative_agent.harness`): a corpus-agnostic review engine
  with a deterministic enforcement core and pluggable LLM judgement.
  - `ReviewPipeline` orchestrating typed LLM calls (classify, per-row doctrine sweeps,
    claim extraction, source-quality, judgement, synthesis) with deterministic assembly.
  - Deterministic checkers: `SeverityPolicy` (advisory/staleness/tier caps and
    multi-support blocker legitimacy), `MeasurementGateChecker`, `SourceQualityChecker`,
    `ConsistencyChecker`, `VerificationLogChecker`, `LabelConformanceChecker`,
    `DecisionGate`, `CycleEscalator`, `ThreatGuard`.
  - Seams as protocols: `LLMClient`, `StateStore`, `Clock`, `CitationResolver`,
    `ReviewAgent`.
- **sutton-review agent** with the full D1–D12 + D6a doctrine table as validated data.
- **CLI** `creative-agent`: `review`, `oracles list|validate|rebaseline`, `agents list`,
  `decisions check`, `state show`, `assets validate`, with a frozen exit-code contract
  (0 clean · 1 ≥Major · 2 Blocker/charter-STOP · 3 review failed · 4 config · 5 unexpected).
- **Durable review state** at `docs/review-log/<artifact-id>.md` with cycle history,
  finding dispositions, and the spec's cycle-3 charter-review escalation.
- **Structured logging** (DEC-F10): namespaced logger, text and JSON formatters, stable
  event names, stage timing, and `--verbose` / `--debug` / `--log-format` flags. Prompts
  and artifact text are never logged.
- **Citation resolution**: arXiv resolver with mechanical author-list diffing and
  `oracles rebaseline`, so a fabricated author list surfaces loudly.
- **Asset validation** (`harness/assets.py`, `creative-agent assets validate`): schema and
  behaviour checks for Claude Code agents, skills, hooks, and settings.
- **Claude Code assets**: `sutton-review` subagent, `add-oracle` / `review-gate` /
  `oracle-rebaseline` skills, SessionStart and PostToolUse hooks.
- **Developer and supply-chain tooling**: `Makefile` as the single definition of the
  quality gates, three-stage `Dockerfile` with a non-root runtime, `.dockerignore`,
  gitleaks config with a CI job and pre-commit hook.
- **Test suite**: 399 tests at ~96% branch coverage, with per-package and per-module
  coverage floors, golden-file output contracts, hypothesis properties, an
  `LLMClient` contract suite across fake/offline/mocked-SDK implementations, and a
  weekly live-SDK verification workflow.
- **Documentation**: C4 architecture views (`docs/architecture.md`), framework decision
  log (`docs/decision-log.md`, DEC-F1..F10), roadmap (`docs/roadmap.md`), and this
  changelog.

### Fixed

Findings from an external code review and two independent audit passes. Each has a
regression test.

- **Path traversal** — `--artifact-id` and the artifact's own front-matter `review-id`
  were used to build file paths without validation; both are now validated as a single
  safe filename component.
- **SSRF** — the WebFetch allowlist absorbed every host found in the untrusted artifact,
  so a planted URL could point the session at loopback, private, link-local, or cloud
  metadata addresses. Hosts are now filtered by policy (settings-driven, with a
  deliberate opt-out for internal mirrors) and rejections are logged.
- **A Blocker could be waived by the model** — a required-section obligation was cleared
  if the LLM asserted the section existed. Section detection now comes from the
  document's own headings; a contradicting claim is logged, never obeyed.
- **Wasted review budget** — verification defects that name no doctrine row selected
  nothing to repair, so the loop re-ran identical calls until failing anyway; it now
  fails immediately as unrepairable.
- **False citation accusations and false clears** — an arXiv API error entry was reported
  as `AUTHOR MISMATCH` (accusing a typo'd id of the fabricated-citation defect class),
  and a source declaring no authors was auto-stamped verified, laundering it past the
  staleness cap.
- **Fail-open sweeps** — one unterminated code fence blinded the duplicate-definition
  Blocker sweep for the rest of a document; an unknown row reference let findings escape
  the staleness and tier caps.
- **Identity instability** — a BOM or CRLF front matter resolved to a different artifact
  id, silently resetting cycle history and the escalation counter on a Windows checkout.
- **Canonicalization gaps** — old-style arXiv ids (`math/0211159`) and DOIs containing
  parentheses canonicalized inconsistently, breaking tool-honesty matching for them.
- **Substring row matching** — the repair loop matched row ids by substring, so `D1`
  inherited `D10`'s defects.
- **Exit-code contract** — configuration failures escaped as code 5 instead of the
  documented 4.
- **Rebaseline state consistency** — an author mismatch cleared `verified` but left a
  stale `last_verified`, misreporting the row as recently checked.
- **Partial state files** — a failed state write could leave a `.tmp` file behind.

### Changed

- **Doctrine is fully data.** Impersonation patterns, decision-log grammar, consistency
  tuning, blocker tiers, pseudo-gates, required-section support kinds, the placeholder
  row id, and the offline artifact class all moved from code into oracle fields. A second
  corpus now needs no code, proven by `TestSecondOracleNeedsNoCode` (which previously
  failed with `unknown artifact class`).
- **Settings coverage.** `fetch_tool_names`, `default_agent`, `max_oracle_depth`,
  `blocked_host_suffixes`, `allow_internal_fetch_hosts`, `log_level`, and `log_format`
  are configuration rather than literals; `permission_mode` is a validated `Literal`;
  list settings accept `a,b`, `a:b`, or JSON from the environment.
- **Coverage gate hardened.** Branch floors as well as line floors, per-module floors for
  the enforcement core, failure (not a warning) when a floor prefix matches nothing, and
  an assertion that the coverage `omit` list matches an approved set.
- **Mutation testing repaired.** The configuration was dead — a string where a list was
  required and a key the tool ignores — and is now guarded by a test that loads it.
- **CI restructured** to call `Makefile` targets, with added secret-scanning and
  container-build jobs.

### Security

- Reviewed artifacts are treated as untrusted input throughout: tool scoping, a
  data-driven fetch allowlist with internal-host filtering, artifact content delimited as
  data in prompts, output laundering for model prose, and tool-honesty verification
  matched against actual successful tool results rather than the model's claims.
- Secret scanning in CI (full history) and pre-commit; the container runs unprivileged and
  ships no credentials.

[Unreleased]: https://github.com/ianshank/Creative-agent-/compare/main...claude/implementation-plan-agent-framework-dbqgvv
