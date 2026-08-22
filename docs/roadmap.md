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

### 3. First real re-baseline of the sutton oracle

Eleven of the thirteen doctrine rows carry `verified: false` — their citations were
transcribed from the specification, not resolved. That is deliberate (the staleness rule
caps findings from unverified rows at Minor), but it means the oracle is currently weaker
than it looks. Run `make rebaseline`, resolve what the resolver cannot reach by hand per
the `oracle-rebaseline` skill, and expect some rows to stay unverified — for a talk or an
unpublished preprint, that is the correct end state, not a failure.

## Near term

### 4. Enforce the tool scope in the SDK session, or narrow the claim — **WebFetch half done**

**Done (DEC-F11a):** a `PreToolUse` hook in `ClaudeSDKAdapter` now denies any `WebFetch`
call whose host isn't in the review's computed allowlist — the allowlist was previously
advisory only (stated in the system prompt, never enforced; only the downstream
tool-honesty check would later refuse to credit resulting "evidence," after the call had
already run). Uses `PreToolUse`, not `can_use_tool`: this project's
`permission_mode="dontAsk"` means `can_use_tool` is very likely dead code here, since it
only fires when permission evaluation would otherwise prompt, and `dontAsk`'s entire
purpose is to skip that. Implemented and unit-tested against the mocked-transport
`ClaudeSDKAdapter` with no API key needed; only final live confirmation is still
owner-blocked (item 1). See `docs/decision-log.md` DEC-F11.

**Still open (DEC-F11b):** `ThreatGuard.allowed_read_roots` is computed and unit-tested but
still not passed to the SDK or rendered into any prompt — read-path scoping (`Read`/`Grep`/
`Glob`) is fully disconnected, not merely advisory. Lower severity than the WebFetch half
(worst case is unintended local file content in laundered, length-capped LLM prose, not
network exfiltration), which is why it shipped second. Same mechanism
(`PreToolUse`), same coverage-boundary discipline (decision logic in `security.py`, not
inline in the `claude_sdk.py` coverage omit) — needs a symlink-safe path check
(`Path.resolve()` + `is_relative_to()`, not string prefix matching) since the artifact
directory being reviewed is untrusted content.

### 5. Schema migration paths

Every durable format is versioned and every loader *rejects* an unknown version — there is
no migration seam. That is honest for v1, but the first `schema_version: 2` will need one,
and adding it retroactively is harder than leaving the hook now. Add a
`_MIGRATIONS: dict[int, Callable]` chain in `harness/oracle.py` and `harness/state.py`,
plus frozen v1 fixtures for the oracle YAML and report contract (state already has one).

### 6. Mutation-testing sandbox

The configuration is correct and mutants generate, but mutmut 3.x copies the tree into
`mutants/` and re-runs pytest there, which needs the package metadata to resolve in the
copy. The weekly job is advisory and says so. Finishing this turns a currently-decorative
signal into a real one for the enforcement core.

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
