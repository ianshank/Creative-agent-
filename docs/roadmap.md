# Roadmap

What is deliberately not built yet, why, and what would have to be true to build it.
Ordered by what unblocks the most. Items marked **blocked** need something outside the
codebase; the rest are ready to pick up.

## Immediately after merge

### 1. Add `ANTHROPIC_API_KEY` to repository secrets — **blocked on the owner**

Nothing has run against the real Claude Agent SDK in CI. The weekly workflow
(`.github/workflows/live.yml`) and `scripts/sdk_spike.py` both skip without it. Until they
run, the SDK adapter's assumptions — `output_format` structured output, `ToolUseBlock` /
`ToolResultBlock` field names — are verified only against mocked transports written from
documentation. The mocked tests will keep passing even if the real surface drifts.

### 2. Fix the repository default branch — **blocked on the owner**

The default branch is currently the feature branch, an artifact of the repo being empty
when the first push landed. Consequences: CodeRabbit skips auto-review, clones land on the
feature branch, and `main` is not treated as primary. Fix in Settings → General.

### 3. First real re-baseline of the sutton oracle — **in progress, one row down**

Ten of the thirteen doctrine rows still carry no verified source; their citations were
transcribed from the specification, not resolved. That is deliberate (the staleness rule
caps findings from unverified rows at Minor), but it means the oracle is currently weaker
than it looks.

**D2 fixed** (arXiv:2212.10420, "Settling the Reward Hypothesis"): the transcribed author
order was wrong — Bowling/Martin/Dabney/Abel instead of the paper's own
Bowling/Martin/Abel/Dabney (Dabney and Abel transposed). Found and fixed by independently
fetching the paper rather than deferring to a future rebaseline run — a live instance of
the exact defect class this oracle's own header comment warns about. A synthetic-oracle
round-trip test (`tests/unit/test_severity.py::
TestStalenessCap::test_rebaselining_a_rows_source_removes_the_staleness_cap`) now proves a
`verified: false → true` transition actually changes downstream severity capping, not just
the loader's shape — nothing tested that integration path before.

**Remaining, deliberately not attempted in one pass:** run `creative-agent oracles
rebaseline sutton --dry-run`, then resolve what the resolver reaches automatically and what
the `oracle-rebaseline` skill's manual fallback reaches for the rest. Ship incrementally
(one row or a small batch per PR), not as one big-bang PR against a live external API — a
single bad resolution or transient rate-limit shouldn't block or force a partial revert of
the whole batch, and the skill's own docs already anticipate some rows staying unverified
as the correct end state, not a failure, for a talk or an unpublished preprint.

## Near term

### 4. Enforce the tool scope in the SDK session — **done (DEC-F11), pending live confirmation**

A `PreToolUse` hook in `ClaudeSDKAdapter` now denies both halves of what DEC-F9 only stated
in the prompt: a `WebFetch` call whose host isn't in the review's computed allowlist
(DEC-F11a), and a `Read`/`Grep`/`Glob` call whose resolved path escapes the review's
computed read roots (DEC-F11b — this half was previously fully disconnected, not merely
advisory: `ThreatGuard.allowed_read_roots` was computed and unit-tested but never reached
the SDK or any prompt at all). The path check resolves both the candidate and the roots
before comparing (`Path.resolve()` + `is_relative_to()`, not string prefix matching), so a
symlink inside an allowed root that points outside it is caught — the artifact directory
under review is untrusted content.

Uses `PreToolUse`, not the SDK's `can_use_tool` callback: this project's
`permission_mode="dontAsk"` means `can_use_tool` is very likely dead code here, since it
only fires when permission evaluation would otherwise prompt, and `dontAsk`'s entire
purpose is to skip that. `PreToolUse` fires on every tool call unconditionally. The
decision predicates (`is_fetch_allowed`, `is_path_within_roots`) live in
`harness/security.py`, which is coverage-counted, rather than inline in `claude_sdk.py`
(the one approved coverage omit, DEC-F8) — a broken check there could otherwise ship
invisibly to the gate.

Implemented and unit-tested entirely against the mocked-transport `ClaudeSDKAdapter`, no
API key needed. What's left is **only** the final live confirmation that the real SDK
invokes the hook the way the mocked transport says it does — that's owner-blocked on item
1, same as everything else needing `ANTHROPIC_API_KEY`. See `docs/decision-log.md` DEC-F11
for the full rationale, including the accepted residual risk (a WebFetch redirect chain to
an internal host is invisible to a hook that only sees the initial URL — documented, not
solved, as over-engineering for a single-user offline reviewer).

### 5. Schema migration paths

Every durable format is versioned and every loader *rejects* an unknown version — there is
no migration seam. That is honest for v1, but the first `schema_version: 2` will need one,
and adding it retroactively is harder than leaving the hook now. Add a
`_MIGRATIONS: dict[int, Callable]` chain in `harness/oracle.py` and `harness/state.py`,
plus frozen v1 fixtures for the oracle YAML and report contract (state already has one).

### 6. Mutation-testing sandbox — **done**

**The suspected cause was wrong.** It wasn't package metadata — reproducing the failure
locally showed `mutmut` only copies the four files it mutates into `./mutants`, not the
rest of the package, so any test importing outside them (`test_assets.py` importing
`creative_agent.cli`, `test_project_config.py` importing `scripts.check_coverage_floors`)
failed collection before a single mutant ran. Fixed with `also_copy` (mirrors the package
and `scripts/`) and by narrowing `pytest_add_cli_args_test_selection` to the four direct
unit-test files for the mutated modules rather than all of `tests/unit/` — the broader
selection also pulled in tests whose behavior depends on `git rev-parse --show-toplevel`
resolving to the *sandbox* root, which silently validates the real repo instead inside the
nested copy.

Along the way: a hypothesis property test needed
`suppress_health_check=[HealthCheck.differing_executors]` (mutmut's multi-process re-import
model triggers a documented hypothesis/mutation-tool interaction, not real flakiness), and
`SeverityPolicy.cap_all` turned out to have zero *direct* unit coverage — only indirect
coverage through pipeline integration tests outside the scoped selection — so its mutants
surfaced as `no_tests` until three unit tests were added.

**Result:** 452/452 mutants killed. `docs/mutation-baseline.json` checks that in;
`scripts/check_mutation_baseline.py` fails the weekly job on any regression in
survived/no_tests/suspicious/timeout counts. `continue-on-error: true` is gone — this is a
real gate now, not a decorative signal.

### 7. Cost and latency budgets under real load

`max_budget_usd`, `max_turns`, and `llm_timeout_seconds` are plumbed but never exercised
against a real review. A per-row sweep over thirteen rows plus five other call kinds is
roughly twenty LLM calls per cycle; nobody has measured what that costs or how long it
takes on a large blueprint. Measure before recommending the tool for routine use.

## Longer term

### 8. The `adversarial-reviewer` companion

The specification names a sibling agent that reviews **diffs** where sutton-review reviews
**designs**. The plugin seam exists and the registry takes it in one line, but the review
model is genuinely different: a diff has no artifact class, no doctrine sweep in the same
sense, and its state is a PR rather than a document. Worth building only once the design
reviewer has been used enough to know which parts of the harness generalize.

### 9. Multi-oracle review

A blueprint could plausibly be reviewed against two corpora at once (say, an RL doctrine
and a safety-engineering doctrine). Nothing in the harness assumes a single oracle except
the CLI's `--oracle` flag and the report contract's single `oracle_id`. The interesting
question is how severities from different corpora combine, which is a product decision
before it is an engineering one.

### 10. Windows support

`harness/state.py` imports `fcntl`, which does not exist on Windows, so the CLI is
unimportable there; CI is Linux-only. Either add a `windows-latest` leg and a portable
locking abstraction, or state POSIX-only in the README. The CRLF/BOM identity bug fixed on
this branch was specifically a Windows-checkout failure, so the exposure is real even for
Linux-hosted reviews of Windows-authored documents.

### 11. Concurrency and multi-writer state

`FileStateStore` takes an advisory lock and assumes a single writer per artifact. Two
reviews of the same artifact on different machines, or state on a branch that is later
rebased, will produce cycle histories that do not merge cleanly. Document the assumption
or move state to a store that can arbitrate.

## Explicitly not planned

- **Approving or merging on the reviewer's behalf.** The escalation rule hands recurring
  disagreements to the owner by design; automating the disposition would defeat it.
- **Softening any output.** The verification-log failure mode (exit 3) and the
  "never soften" rule are load-bearing; a "summary mode" that drops the verification log
  would make the report unfalsifiable.
- **A hosted service.** The tool reads untrusted documents and holds an API key; running
  it as a shared service is a materially different threat model than the current one.
