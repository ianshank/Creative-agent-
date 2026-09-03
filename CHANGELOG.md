# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims at
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Durable formats carry their own version numbers independent of the package version —
oracle YAML `schema_version`, review-state `schema_version`, report `contract_version`,
and the LLM call contract — so a package release can be breaking for code while remaining
compatible for data, or the reverse. Changes to any of those are called out explicitly.

## [Unreleased]

Work on top of the PR #7 merge. The first entries in this section were documentation and CI
only; everything added since changes harness behaviour, published severities and the
exit-code contract. The exit-code table gains code `6` — see the frozen-contract entry under
**Added** before pinning a CI consumer to this revision.

### Changed

- **An unverified doctrine row is now capped from the first review.** The grace budget
  `freshness.max_rebaselines_without_verification` defaults to `0` rather than `2`, and the
  shipped `sutton.v2.yaml` sets it explicitly to `0` with the reason inline. This changes
  published severities: a finding whose only support is a transcribed citation nobody has
  resolved is now held to `severity_policy.unverified_row_cap` instead of reaching Blocker.
  The field, its schema position and the capping mechanism are unchanged, so an operator who
  deliberately wants a grace window raises it and owns that choice, and no data migration is
  required. Fail-closed is the correct default for a reviewer that refuses to soften its own
  output. DEC-F13.

- **`max_budget_usd` now caps a run, not a call.** It was passed to `ClaudeAgentOptions` per
  call, so the setting permitted itself multiplied by the number of calls a review makes:
  18 on the happy path, and up to roughly 144 provider calls once the classify re-probe, the
  repair loop and `_call`'s own schema-retry loop compound. A $2.50 setting permitted about
  $360. `ReviewPipeline` now sums `cost_usd` across every attempt in `sweep.calls` and checks
  the total *inside* `_call`'s attempt loop, before each provider call, so one logical call
  cannot burn several times what is left. Exhaustion raises `BudgetExceededError` and the run
  publishes nothing. What the SDK receives as its own per-call cap is the **remaining** run
  budget, carried on `AssembledPrompt`, not the raw setting: a pre-call check cannot know
  what the call will cost, so a $2 budget with $1.90 calls would otherwise spend $3.80, and
  passing the raw setting made one number mean "per call" in the adapter and "per run" in the
  pipeline, for a practical bound of roughly twice the setting (DEC-F19). A backend that
  reports no cost (`OfflineLLMClient`, `FakeLLMClient`) contributes zero rather than aborting
  the run. DEC-F17.

- **`llm_timeout_seconds` is live.** It was declared in `HarnessSettings`, documented in
  `config/settings.example.yaml`, and used nowhere in `src/` — dead configuration, so a hung
  provider call blocked a review indefinitely. Each call now runs under `asyncio.timeout`;
  expiry raises `LLMTimeoutError`. DEC-F17.

- **Review state is detect-and-fail, not single-writer-by-assumption.** `StateStore.save`
  takes an optional `expected_cycle` and the pipeline always passes the cycle it loaded; a
  mismatch aborts the run. The protocol widened rather than broke — the keyword defaults to
  `None`, so every existing implementation and test double keeps working. `reset()` now takes
  the same lock, which `--reset-state` previously raced. DEC-F14 supersedes the part of
  DEC-F4 that said single-writer was "assumed and documented".

- **The `PreToolUse` hook denies by default.** It matches every tool rather than four names,
  and a tool is allowed only if it is explicitly scoped (`WebFetch` against the computed host
  allowlist, `Read`/`Grep`/`Glob` against the computed read roots and their glob patterns) or
  named in the new `unscoped_tools` setting. An unrecognised tool is denied and logged. This
  is a behaviour change for any deployment that had widened `agent_tools`: a tool added there
  and not to `unscoped_tools` is now refused at the call boundary. DEC-F15.

- **`pytest` runs with `--strict-markers` and `filterwarnings = ["error"]`.** A mistyped
  `@pytest.mark.live` used to deselect a test from every run with no signal, and a
  dependency's DeprecationWarning printed among 500 dots is not a notice anyone reads. The
  suite raises no warnings today, so the allowance list is empty.

- **`docs/roadmap.md` rewritten as a sequenced development plan**, after a peer review that
  re-verified every claim the previous version made against the code. Four of its
  statements were wrong and two of its priorities were inverted, and the corrections are
  recorded inline rather than dropped. The review found seven places where a documented
  guarantee does not hold — among them a tool-honesty check satisfiable by a fetch that
  never happened, a staleness severity cap that never fires because its trigger is coupled
  to a counter that is still zero, and a concurrent review that can erase the cycle-3
  charter-review escalation — and nine gates that report success while verifying nothing,
  including a mutation gate that passes on zero mutants. Every finding carries a
  `file:line` and was reproduced. Controls that were attacked and held are recorded too,
  so the fixes are not read as a verdict on the whole boundary. This entry is documentation
  only — no harness, CLI or oracle behaviour changed, and none of the findings are fixed
  here; the plan says what to change and in what order.

- **Pinned GitHub Actions versions bumped** by Dependabot, across `ci.yml`, `live.yml` and
  `mutation.yml`: `actions/checkout` 4.2.2 to 7.0.1, `actions/upload-artifact` 4.4.3 to
  7.0.1, `actions/github-script` 7.0.1 to 9.0.0, and `docker/build-push-action` 6.9.0 to
  7.3.0. These merged as PRs #3 through #6 against the old default branch and reach `main`
  with this change; they alter CI, not the package. Every action stays pinned to a commit
  SHA, which `tests/unit/test_project_config.py` asserts.

### Added

- **Exit code `6`, `RUN_ABORTED` — a frozen-contract change.** The exception-to-exit-code
  table is a machine-facing contract in the same sense as the durable format versions above:
  frozen means a change must be deliberate and visible, not that it can never happen. This
  is a versioned addition, recorded in DEC-F17, `README.md`, `docs/architecture.md` and the
  `test_exit_code_values_are_frozen` tripwire. `ExitCode.RUN_ABORTED = 6` means the run
  stopped before producing a verdict: nothing was published, no state was written, and a
  retry is meaningful. It is carried by a new `RunAbortedError` base and three subclasses —
  `BudgetExceededError`, `LLMTimeoutError` and `StateConflictError` (DEC-F14). It is
  deliberately *not* code 3: `REVIEW_FAILED` says the review ran and its verification log
  could not be completed, which is a statement about the artifact, and collapsing the two
  would tell a CI consumer to treat a transient budget stop as a finding about the document.
  Existing codes 0 through 5 are unchanged. A consumer that treats any nonzero code as
  failure needs no change; one that branches on the value should route 6 to a retry.

- **A DOI resolver, and a composite resolver that dispatches to whichever backend can
  identify a source.** `ArxivCitationResolver` returns `skipped` whenever `arxiv_id` is
  absent, so four of the shipped oracle's sources — D6's Nature DOI and D9's Elsevier DOI
  among them — had no code path to resolution at all. With the staleness cap now firing that
  is a severity bug rather than a gap: a peer-reviewed paper's findings would be capped
  because nobody transcribed a resolvable identifier, not because the evidence is weak.
  `CrossrefCitationResolver` mirrors the arXiv backend's contract including both of its
  hard-won refusals — a source declaring no authors is `unreachable` rather than
  auto-verified, which would otherwise launder it past the cap, and a transport or shape
  failure is `unreachable` rather than `mismatch`, which would accuse a correct citation of
  the fabricated-citation defect class. `CompositeCitationResolver` reports `skipped` only
  when every backend skipped, so a source with no resolvable identifier reports honestly
  instead of borrowing another backend's failure. Wired into `oracles rebaseline` (arXiv
  first, Crossref second); review-time resolution is still deliberately out of scope.

- **An offline ceiling banner.** An offline review is structurally incapable of failing on
  artifact content in the default `auto` mode, and nothing said so anywhere a user would
  look. `OfflineLLMClient` returns no claims, so `MeasurementGateChecker` — the whole
  quantitative bar, and the only source of a compute-budget Blocker — scores nothing; every
  doctrine row comes back `not_applicable`; and offline classify recommends `advisory`, which
  caps every finding at the oracle's advisory ceiling and exits 0. A document with three
  genuine Blockers and an admission that no baseline was measured exits 0 and reports nothing
  above Info, so anyone wiring `--offline` into CI as a gate is running a pass-through.
  `creative-agent review --offline` now prints that plainly, naming `--mode conformance` as
  the gating alternative. It goes to **stderr**: the rendered report on stdout is a
  byte-stable published contract, and a test asserts the banner stays out of the
  `--output-json` payload too.

- **Schema migration seams for the two durable read formats** (`harness/migrations.py`).
  Every durable format carries a version and both loaders rejected an unknown one outright,
  so there was no point at which a `schema_version: 2` file could be upgraded on read.
  `MigrationChain` applies an ordered chain of raw-dict upgrade steps between the version
  read and `model_validate`, and both `harness/oracle.py` and `harness/state.py` route
  through it. The chain is empty today — v1 is current for both, so `migrate` is an identity
  pass whose plumbing is tested, and the first real migration registers a step rather than
  inventing a mechanism under time pressure. A gap in the chain truncates
  `supported_versions` rather than silently skipping a version, so a half-registered chain
  cannot claim to read a file it would mangle. DEC-F18.

- **A frozen v1 oracle fixture** (`tests/fixtures/oracle/v1-example.yaml`), alongside the
  existing `tests/fixtures/state/v1-example.md`. `models/base.SchemaModel` sets
  `extra="forbid"`, so a migration step must emit exactly the field set of the version it
  upgrades *to*, which means each future step needs a frozen copy of the version it upgrades
  *from*. `tests/factories.py::make_oracle` builds against the live model and therefore
  follows it wherever it goes; it can never prove a v1 file still loads. The fixture is frozen
  bytes with a header saying not to regenerate it. Both durable read formats now have one.

- **`oracle.unverified_rows_uncapped` at WARNING.** `OracleLoader` now names the rows when a
  loaded corpus combines blocker-tier rows with no verified source and a nonzero grace budget
  — the exact combination that let ten unresolved rows go uncapped without anyone noticing.
  Silence about weak doctrine is how DEC-F13's defect survived; this makes it greppable.

- **`security.pretooluse_denied` and `security.websearch_issued`.** Every hook denial is
  logged with the tool name and the reason. `WebSearch` remains in the default tool set with
  its residual risk accepted and documented (DEC-F15): its query is unconstrained, so an
  artifact that can steer the model's search terms has an outbound channel. The event records
  the query's *length* and never its text, per DEC-F10's rule that prompts and artifact text
  are never logged.

- **New settings**, all documented in `config/settings.example.yaml`: `unscoped_tools` (tools
  the `PreToolUse` hook allows without a target check), `identifier_authority_hosts` (which
  hosts may vouch for a scholarly identifier, so an institutional mirror or a DOI proxy is a
  settings change rather than a code change), `crossref_api_url`, and `citation_user_agent`
  (Crossref rate-limits anonymous traffic into a slower pool; the default deliberately is not
  an operator email, since an address belongs in an outbound header only when someone puts it
  there on purpose).

- **Platform metadata.** `pyproject.toml` gains `Operating System :: POSIX`, `POSIX :: Linux`
  and `MacOS` classifiers plus the three supported Python versions. The project previously
  carried no platform statement anywhere, so it silently claimed portability it has never
  verified. Both known Windows breaks are fixed — the `fcntl` import moved behind
  `harness/filelock.py`, and `assets validate`'s execute-bit check is POSIX-gated — but no
  Windows CI leg exists, so the classifier states what is tested rather than what might work.
  `docs/roadmap.md` 4.2 still holds the full-support option and its honest cost.

- **`inspect-state` skill**: documents the CLI commands that had no skill/agent coverage
  (`oracles list`, `agents list`, `decisions check`, `state show`, `assets validate`, plus
  `review --mode`/`--output-json` and the global `--log-format` flag). A new
  `TestSkillContracts` case invokes every command it names through the real Typer app and
  asserts each still resolves — in-process via `CliRunner`, in the same spirit as
  `TestHookBehaviour` running the real hooks rather than only checking they exist. (An
  earlier revision of this entry said "subprocess-confirms"; it does not, and the test's own
  docstring says so.) A purely descriptive skill with no runnable check was a real gap in
  this repo's own documentation-honesty philosophy.

- **DEC-F11a: real WebFetch scoping.** `ThreatGuard`'s fetch-domain allowlist (DEC-F9)
  reached the model only as prose in the system prompt — nothing stopped a `WebFetch` call
  to an off-allowlist host from actually executing; only the downstream tool-honesty check
  would later refuse to credit the resulting "evidence," after the call had already run.
  A `PreToolUse` hook in `ClaudeSDKAdapter` now denies the call itself. Uses `PreToolUse`
  rather than the SDK's `can_use_tool` callback because this project's
  `permission_mode="dontAsk"` means `can_use_tool` is very likely never invoked here (it
  only fires when permission evaluation would otherwise prompt); `PreToolUse` fires
  unconditionally. The decision predicate (`is_fetch_allowed`) lives in `harness/security.py`
  (coverage-counted) rather than inline in `claude_sdk.py` (the one approved coverage
  omit, DEC-F8), so a broken check can't ship invisibly to the gate.

- **DEC-F11b: real Read/Grep/Glob scoping.** `ThreatGuard.allowed_read_roots` was computed
  and unit-tested but never reached the SDK or any prompt — read-path scoping was fully
  disconnected, not merely advisory. The same `PreToolUse` hook now also denies a
  `Read`/`Grep`/`Glob` call whose resolved path escapes the review's computed roots
  (artifact directory, oracle directories, and `--artifact-repo` when given). The
  predicate (`is_path_within_roots`) resolves both the candidate path and the roots before
  comparing (`Path.resolve()` + `is_relative_to()`), so a symlink inside an allowed root
  that points outside it is caught — the artifact directory under review is untrusted
  content, and a string-prefix check would miss that. Grep/Glob's `path` argument is
  optional and falls back to the session's working directory when omitted. `docs/decision-
  log.md` DEC-F11 now covers both halves.

- **Mutation testing sandbox fixed; the weekly job is now a real gate, not advisory.**
  `mutmut run` copies the four `[tool.mutmut].source_paths` files into `./mutants` and
  re-runs pytest there — the roadmap suspected a package-metadata resolution problem, but
  reproducing the failure locally showed the actual cause: `mutmut` only copies files it
  mutates, so `test_assets.py` importing `creative_agent.cli`, `test_project_config.py`
  importing `scripts.check_coverage_floors`, and `.claude/` asset-validation tests all
  failed collection with `ModuleNotFoundError` before a single mutant ran. Two changes fix
  it: `also_copy = ["src/creative_agent", "scripts"]` mirrors the rest of the package the
  scoped tests need, and `pytest_add_cli_args_test_selection` now runs only the four direct
  unit-test files for the mutated modules (`test_severity.py`, `test_gates.py`,
  `test_verification.py`, `test_consistency.py`) rather than all of `tests/unit/` — the
  broader selection was also pulling in tests whose behavior depends on
  `git rev-parse --show-toplevel` resolving to the *sandbox* root
  (`TestHookBehaviour`'s subprocess-run hooks), which silently validated the real repo
  instead inside the nested copy.
  Also found and fixed along the way: a hypothesis property test failed its own
  `differing_executors` health check under mutmut's multi-process re-import model (a
  documented hypothesis/mutation-tool interaction, not real flakiness — suppressed with a
  comment explaining why); `SeverityPolicy.cap_all` had zero direct unit coverage (only
  indirect, through pipeline integration tests outside the scoped selection), so its four
  mutants survived as `no_tests` — three new unit tests close that.
  Result: **452/452 mutants killed**, checked into `docs/mutation-baseline.json` and
  enforced by the new `scripts/check_mutation_baseline.py`, which fails the job on any
  regression in survived/no_tests/suspicious/timeout counts rather than the previous
  `continue-on-error: true` silently swallowing them. (Now **470/470**: the population grew
  when DEC-F12's authority binding added real branching to `verification.py`, and the
  baseline records the new `total` so the population floor moves up with the code — see the
  gate repairs under **Fixed**.)

### Fixed

- **The staleness severity cap never fired.** `OracleRow.is_stale` returned
  `freshness.rebaseline_count >= freshness.max_rebaselines_without_verification`, and the
  shipped `sutton.v2.yaml` carried `rebaseline_count: 0` against a threshold of `2`. `0 >= 2`
  is False for every row, so no row was ever stale and `severity_policy.unverified_row_cap`
  never applied to anything. Six rows with no verified source sat at a tier in `blocker_tiers`
  — D4, D5 and D11 at AP, D8, D9 and D10 at PR — so a finding whose only support was an
  unresolved transcribed citation could publish as a full Blocker and exit 2. The cost is the
  one this oracle's own header comment exists to name: transcription without resolution is
  the defect class the file is for, and the protection against it was inert. See **Changed**
  above for the fix. DEC-F13.

- **Three oracle rows rebaselined by hand against the papers' own title pages**, which is the
  same mechanical author diff `oracles rebaseline` performs. arXiv:2208.11173, "The Alberta
  Plan for AI Research" (D5 and D11) — Richard S. Sutton, Michael Bowling, Patrick M.
  Pilarski, matching the YAML exactly; D11's non-obvious claim checks out too, since step 11
  of the roadmap really is "Prototype-AI III: Oak", spelled that way in the paper.
  arXiv:2202.03466, "Reward-Respecting Subtasks for Model-Based Reinforcement Learning" (D8)
  — all seven authors match in order, David Szepesvari included, whom the Alberta Plan's own
  bibliography omits, so the spec was right to emphasise the full list. Each source now
  records how it was resolved, because `arxiv.org` is blocked by the review environment's
  egress proxy and the text had to be fetched through a mirror. D9 and D10 were corroborated
  only from other papers' reference lists and are deliberately left `verified: false`.

- **The tool-honesty check could be satisfied by a fetch that never happened.**
  `VerificationLogChecker._evidence_identifiers` canonicalized the *requested target string*,
  never the fetched resource, and `canonical.canonicalize` matches an arXiv id or DOI as a
  substring anywhere in that string. Three different targets therefore credited
  `fetched=True` for the same paper: the genuine `https://arxiv.org/abs/2401.12345`, a decoy
  `https://attacker.example/x?src=arxiv.org/abs/2401.12345` on any host the artifact's own
  bibliography put on the allowlist, and a local `Read` of `refs/arxiv.org/abs/2401.12345.md`
  inside the artifact repository — a read root whose file names the artifact's author
  controls, and which needs no network at all. This is the control `docs/architecture.md` §8
  called "the control that makes the verification log worth reading," and canonicalization is
  exactly what made it fake-satisfiable. Two rules now, both deterministic. **Authority
  binding:** a canonical identifier enters the observed-evidence set only from a successful
  fetch-class result whose target is an `http`/`https` URL *and* whose host is a registrar
  for one of the configured identifier schemes, matched on the host or a dot-anchored
  subdomain so `notarxiv.org` cannot pass as `arxiv.org` while `export.arxiv.org` does; the
  identifier is read from the host and path only, never the query string or fragment. A
  non-URL target contributes no identifier. **Claims matched by what they carry:** any entry
  whose `canonical_id` *or* `source_url` canonicalizes to a scholarly identifier is matched
  by identifier — raw-string equality cannot satisfy it, which is what let a local read back
  a claim about a paper — and an evidence target that itself canonicalizes can never be
  credited as plain-string evidence. Only an entry carrying no identifier at all is satisfied
  by an exact target match, so reading the artifact under review keeps working.
  `canonicalize` itself is unchanged: it is used for identity bucketing in the source-quality
  checker and the rebaseline resolver, where a permissive substring match is correct. The
  defect was trusting extraction from an attacker-chosen string as evidence of retrieval, so
  the fix lives at the evidence boundary. DEC-F12, as corrected by DEC-F19 — the first
  implementation was host-bound rather than retrieval-bound and keyed on `canonical_id`
  alone; see the corrections below for what that still allowed.

- **A concurrent review could erase the cycle-3 charter-review escalation.** `FileStateStore.
  load` took no lock, `save` locked only the tmp-write and `os.replace`, and the pipeline
  computed its escalation verdict from a snapshot loaded up to 144 provider calls earlier.
  Two reviews of one artifact both read cycle N, both wrote N+1, and the second `os.replace`
  discarded the first's history wholesale. The lost record is the lesser harm:
  `CycleEscalator.check` counts recurrences from the stale snapshot, so a Major that recurred
  in the concurrent run is invisible and the charter-review STOP can fail to fire. It
  reproduces with two terminals on one machine — `flock` genuinely serialises the writes
  there, which is why it looked safe — and `CLAUDE.md` advertises `/loop 30m`, making
  concurrent invocation a documented workflow. `save` now re-reads the stored cycle under the
  lock it already holds and raises `StateConflictError` (exit 6) when it no longer matches
  what the caller loaded. The run fails rather than retrying: by the time `save` is reached
  the verdict, the `ReviewReport` and the rendered markdown are all built from the stale
  snapshot, so retrying the write would publish a verdict computed against history that no
  longer exists. Corrupt state found at that point is reported as a conflict rather than a
  `StateCorruptError`, so a concurrent writer's partial file is not misattributed to the
  caller. DEC-F14.

- **Symlink-following on the state temp and lock files.** Both opens followed an existing
  symlink, so a symlink planted at either path turned an atomic state write into an
  arbitrary-file overwrite with partly attacker-influenced content, plus a truncate primitive
  via the lock file. The temp file is now unlinked and re-created with `O_EXCL` (`unlink`
  removes a symlink itself, never its target) and the lock file opens with `O_NOFOLLOW`;
  content is fsynced before the rename, so "atomic" survives a crash and not merely an
  interleaving. `os.replace` onto the final path was already safe. Writing the tests for the
  extracted module found a further bug in the original code: cleanup of the partial temp file
  was scoped to `except OSError`, so any non-`OSError` failure mid-write left the fragment on
  disk — the case the pre-existing "partial state files" fix was meant to cover. It is a
  `finally` now. All of this moved into a new `harness/filelock.py`, which also removes the
  single POSIX-only module-scope import that made `review` and `state show` unimportable off
  POSIX. Locking stays advisory and single-host by design; correctness against a concurrent
  writer comes from the cycle check above, not from the lock.

- **Three ways past the `PreToolUse` hook DEC-F11 introduced.** `Glob` takes a required
  `pattern` and an optional `path`, and the hook read only the path keys, so with `path`
  absent it validated the session cwd and ignored the pattern entirely:
  `Glob{"pattern": "/etc/**/*"}`, `{"pattern": "/home/someone/**/*.pem"}` and
  `{"pattern": "../../**/*"}` were all allowed, giving path enumeration outside the read
  roots. DEC-F11b claimed `Glob` was covered; it was not. The hook also returned `{}` (allow)
  for every tool it did not recognise, and its `HookMatcher` only fired for four tool names,
  so nothing else was ever inspected. And `tool_input.get(key)` is a truthiness test, so
  `Read{"file_path": ""}` silently degraded to the cwd check and was allowed. Pattern scoping
  now takes the pattern's longest leading literal segment, joins it to the call's base
  directory, resolves it and applies the same containment predicate — `*.md` resolves to the
  base directory and is allowed, an absolute or traversing pattern is denied. Per-tool
  argument shapes are data, which is what keeps the Grep/Glob distinction survivable: `Glob`'s
  `pattern` is a path glob, `Grep`'s `pattern` is a regex and only its `glob` filters paths,
  and `Read`'s `file_path` is required where the others' `path` is not. A present-but-empty
  path key is a denial, because a malformed call is not a safe call. A null or non-mapping
  `tool_input` no longer raises inside the hook, since whether the SDK reads a hook exception
  as allow or deny is not something to depend on. DEC-F15.

- **The internal-host filter missed every non-canonical IPv4 form, and RFC 6598.**
  `ipaddress.ip_address` accepts only canonical dotted quads, so `127.1`, `127.0.1`,
  `0x7f.0.0.1` and `0177.0.0.1` fell through to the name branch and were classified as public
  — while `getaddrinfo` and `inet_aton` in any real fetcher expand every one of them to
  127.0.0.1. `http://127.1/admin` in an artifact bibliography joined
  `fetch_domain_allowlist`, `is_fetch_allowed` permitted it, and `rejected_fetch_hosts`
  reported nothing, so the audit log was silent. Hosts now normalise through
  `socket.inet_aton`, the same parser those callers use, which closes the gap at its source
  rather than by pattern-matching the known forms. `not is_global` is checked alongside the
  explicit categories, which brings in the 100.64.0.0/10 carrier-grade NAT range the previous
  union missed outright; neither predicate is sufficient alone, since IPv4 multicast is
  `is_global`. Separately, `is_fetch_allowed` validated the host and never the scheme, so
  `file://arxiv.org/etc/passwd` passed on every review — `arxiv.org` and `doi.org` are on
  every allowlist unconditionally, so that form needed no cooperation from the artifact at
  all. Only `http` and `https` are fetchable now. DEC-F15.

- **Model prose could forge the structure of the published report.** `launder_prose` stripped
  `[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]`, which lets `\n`, `\r`, `\t`, zero-width and bidi
  characters straight through, and `_md_escape` was applied only inside table cells and never
  to `\r`. A rendering run produced a second `**VERDICT**` line, a forged
  `## Findings / No findings.` section and a corrupted verification row, all from prose the
  untrusted artifact can steer. The deterministic renderer is the stated guarantee that the
  model never emits the report; indirectly, it did. `launder_prose` now folds every line
  break and tab to a single space — folded *before* control-character deletion, so `\v` and
  `\f` do not splice two words together — removes Unicode format characters (category `Cf`,
  covering zero-width spaces, bidi overrides and isolates, and the BOM), maps non-breaking
  spaces to spaces, and collapses whitespace runs. A bidi override renders a finding in the
  reverse of the text stored in review state, so the report and the audit trail disagree
  while both look correct. Laundering is idempotent, which matters because prose passes
  through once per repair-loop iteration. The renderer no longer relies on its caller having
  laundered correctly: it escapes the verdict headline, the escalation message, the
  `what_survives` and `residual_risks` bullets, the scope-item references, the findings
  table's `doctrine_refs` and `gate_refs`, and the model-supplied `row_id` — not just table
  cells — and `_md_escape` now handles `\r` as well as `\n` because most markdown renderers
  break a line on a bare carriage return. `scope_items` are laundered at assembly like every
  other prose block. Structural fields the harness assigns itself — `finding_id`, oracle row
  ids — are not model input and are left alone, which a test asserts rather than assumes.
  DEC-F16; the ref lists and the `scope_items` laundering are DEC-F19, because the first fix
  claimed both and delivered neither.

- **Nine checks that reported success while verifying nothing, or credited the wrong
  outcome.** Each passed for a reason unrelated to what it claimed to check.
  - **Mutation baseline.** `scripts/check_mutation_baseline.py` gated only
    `survived`/`no_tests`/`suspicious`/`timeout`, every one of which is 0 when no mutant is
    generated, so an all-zero stats file exited 0 and read as a perfect kill rate. `total` is
    now gated downwards against the baseline with a named tolerance
    (`MAX_POPULATION_SHRINK_RATIO`), a collapse to zero fails outright, and the docstring
    lists the failure mode it previously omitted.
  - **Weekly live-SDK job.** The only live test skips itself without `ANTHROPIC_API_KEY`, so
    `pytest -m live` exited 0 having run nothing — the last run logged
    `1 skipped, 470 deselected` and reported success, and the workflow opens an issue on
    failure, so the absence of issues read as evidence the SDK surface had not drifted. A
    pre-flight step now fails the job with a message before pytest runs; the fork guard is
    unchanged.
  - **Report contract.** The `[Unverified — flagged for human check]` marker and the section
    headings were asserted only by golden files, which `make goldens` regenerates from the
    renderer itself — deleting the marker and re-running that target left the suite green
    with the central honesty guarantee gone. `tests/unit/test_report_contract.py` asserts the
    contract directly with no reference to the goldens. The goldens' Windows-path leak
    pattern was also a dead branch (it required two literal backslashes); fixed, and now
    covered by a test that it matches a real leak.
  - **`assets validate`.** Every walk in `collect()` was guarded by `is_dir()`, so renaming
    `.claude/agents` to `.claude/agent` validated nothing and printed `ok: all assets valid`.
    An empty inventory is now a defect per expected kind, with the kinds as data
    (`EXPECTED_ASSET_KINDS`) rather than literals inside the function — mirroring the oracle
    path, which already refuses to call a run successful when it loaded no oracle files.
  - **PostToolUse hook.** The matcher named `Edit|Write` while `CLAUDE.md` claims the hook
    re-validates "after any edit", so `MultiEdit` and `NotebookEdit` never re-validated data.
    Widened, and the wiring is now tested — the existing hook tests run the scripts by
    subprocess, which bypasses the matcher entirely, so the matcher could have named `Bash`
    and every test would have stayed green.
  - **Container gate.** The `Dockerfile` never copied `CLAUDE.md`, which the suite reads, so
    `make docker-test` failed; the static guard meant to catch exactly that could not see the
    gap, because its scan matched only string literals adjacent to `ROOT /` and `CLAUDE.md`
    is read through a `@parametrize` list. The scan now resolves parametrized path lists via
    AST, the blind spot itself is covered by a test, and CI builds and runs the `test` stage
    so the container gate stops being a purely static assertion.
  - **Leaked-review-state detector.** It skipped when `docs/review-log/` was absent. The
    directory is tracked via `.gitkeep`, so removing the placeholder disabled the one
    detector for the regression that had already reached a pull request once. It asserts now.
  - **CI-covers-`make gate`.** The test grepped only for `run: make <target>`, so any check
    added to CI as a raw step was invisible to the comparison — which is why the container
    gap went unnoticed. Every step is now accounted for: setup, a `gate` dependency, or a
    declared exception carrying its reason, and each declared exception must match a real
    step so a stale entry cannot quietly widen what the test accepts.
  - **A false *kill*, opened by the warnings-as-errors change above** and found by re-running
    mutation testing after the DEC-F12 work. Under mutmut's multi-process re-import model a
    garbage-collected `ScandirIterator` surfaces as an unraisable exception at interpreter
    shutdown, and `filterwarnings = ["error"]` promotes that to a test-run failure — which
    mutmut reads as a KILL, so a mutant that actually survived would have been counted as
    caught. That is the same false-green class as a gate passing on zero mutants, arriving
    through a different door. The allowance is scoped to mutmut's own pytest invocation, so
    the main suite keeps warnings as errors, and it errs the safe way: a genuine unraisable
    exception now leaves the mutant surviving and the gate reports it. The baseline moved
    452 → 470 in the same pass, with the reason recorded in the file — authority binding
    added real branching to `verification.py` — because leaving the floor at 452 would have
    let eighteen mutants' worth of enforcement core be deleted without the guard noticing,
    which is the hole the guard exists to close.

- **Four holes an adversarial review found in the fixes above — DEC-F19.** DEC-F12 through
  DEC-F18 were implemented and then attacked. Four of those entries asserted more than their
  code delivered, so the corrections are recorded rather than edited into the originals: what
  was believed at the time is part of the record.
  - **A forged Blocker row reached the published report.** DEC-F16 said the renderer "escapes
    every model-supplied field it emits". It did not escape `doctrine_refs` or `gate_refs`,
    which are joined and interpolated raw into the findings table's Doctrine/gate cell.
    `gate_refs` is copied verbatim from the model's `CandidateFinding` and is an
    unconstrained `list[str]`, so one entry closed the cell and opened a forged **Blocker**
    row — the exact defect that entry exists to close, in the exact table it names, surviving
    the fix that named it. Both ref lists now pass through `_md_escape`, and the guard counts
    the table's data rows rather than grepping for an escape sequence.
  - **`scope_items` were never laundered.** DEC-F16 said the pipeline laundered them like
    every other prose block. Nothing did: only the renderer's pipe-and-newline escape ran, so
    a bidi override or a zero-width run reached the report and `max_prose_chars` never
    applied. They now pass through `ThreatGuard.launder_prose` with the rest.
  - **The `Glob` check held only for patterns whose first segment is literal.**
    `glob_pattern_root` trimmed the partial final segment with `rsplit("/", 1)[0]`, which
    returns `""` when the first metacharacter falls inside the first segment — and `""` reads
    as "relative to the base directory", which is allowed. `/etc*/*`, `/h*me/user/.ssh/*` and
    `/*` were all permitted; only `/etc/**/*` was caught, which is the form the original
    tests used. Those tests were written to the implementation rather than against it. The
    trailing separator is kept now, so an absolute pattern stays absolute, and brace or
    bracket groups containing a path separator, and a leading `~`, are refused outright
    rather than guessed at.
  - **The tool-honesty check was opt-out, admitted a decoy on the registrar itself, and would
    have failed legitimate reviews.** Three defects in one control. `canonical_id` is
    optional and the model writes the entry, so the party the check constrains chose which
    branch judged it — omitting the field restored the local-`Read` forgery verbatim.
    `arxiv.org` and `doi.org` are on every computed allowlist unconditionally and both return
    200 for arbitrary paths, so the decoy simply moved to the registrar and
    `arxiv.org/?x=arxiv.org/abs/<id>` was credited. And requiring the host to be an authority
    for the scheme *canonicalization happened to pick* refused
    `doi.org/10.48550/arXiv.<id>` — arXiv's own DOI prefix, and the standard modern citation
    form — so a conformance review citing a preprint through the DOI resolver DEC-F12
    explicitly blesses would have exited 3. A port defeated the match too. See the corrected
    rules in the tool-honesty entry above.
  - **The budget overshoot was understated.** DEC-F17's "a single logical call cannot burn
    several times the remaining budget" was true of the retry loop and false of the run: a
    pre-call check cannot know what the call will cost, so a $2 budget with $1.90 calls spent
    $3.80. Worse, the adapter still passed the raw `max_budget_usd` to the SDK as a *per
    call* cap, so one number meant two different things and the practical bound was roughly
    twice the setting. The remaining run budget is now carried on `AssembledPrompt` and used
    as the backend's per-call ceiling, which bounds the overshoot at the budget itself.
  - **Known and accepted, not fixed:** `identifier_authority_hosts` reaches the honesty
    checker but not `ThreatGuard.fetch_domain_allowlist`, so a configured mirror is accepted
    as evidence while the `PreToolUse` hook still denies the fetch — DEC-F12's "a mirror is a
    settings change" is not yet true end to end, and that is recorded in `docs/roadmap.md`
    rather than half-fixed. Deny-by-default still has no live verification, which needs the
    API key Tranche 0 is blocked on, and `_EVIDENCE_SCHEMES` and `ALLOWED_FETCH_SCHEMES` are
    two literals for one policy.

- **The artifact *path* was as untrusted as its contents, and nothing checked it.** The
  documented `--artifact-repo` flow reviews a checked-out worktree and git carries symlinks,
  so the reviewed repository decides where `docs/design.md` actually points; Typer's
  `dir_okay=False` rejects directories and nothing else. `stat().st_size` reports 0 for a
  character device, so `docs/design.md` symlinked to `/dev/zero` passed the size cap and then
  read unbounded — a plausible out-of-memory from a one-line symlink. `read_artifact` now
  refuses anything that is not a regular file, and, when `--artifact-repo` is given, refuses
  a path the operator located inside that repository that resolves outside it — so a symlink
  at `docs/design.md` pointing at `~/.ssh/id_rsa` is refused rather than read, delimited and
  handed to the model. The containment check is deliberately conditional on where the
  operator pointed: reviewing a document that genuinely lives outside the repository while
  passing `--artifact-repo` so `DecisionGate` reads that repository's decision log is a
  legitimate pattern. Symlinks that stay inside the tree are fine — refusing every symlink
  would break ordinary checkouts to no benefit.

- **Two more gates that looked at nothing.** `ClaudeSDKAdapter.generate()` is the only place
  the real `claude_agent_sdk.query` is bound, and it was verified by nothing: every test,
  including the shared `LLMClient` contract suite's `sdk-mocked` leg, called the private
  `_run` with a hand-written transport; the module is coverage-omitted under DEC-F8; and the
  live test skipped for want of an API key. Three independent gates all declined to look at
  the one function that touches the SDK, so a renamed or re-signatured entry point would have
  surfaced only in a user's review. `generate()` is now exercised directly, including the
  `ImportError`-to-`LLMTransportError` branch. Separately, the `PreToolUse` enforcement tests
  sat behind `pytest.importorskip("claude_agent_sdk")`, and `claude-agent-sdk` is an optional
  extra — any environment synced without `--all-extras` dropped the entire sandbox-escape
  class and reported green, CI being safe only because `make install` happens to pass that
  flag. The skips are gone, so a missing extra fails at import, and a test states the
  requirement rather than leaving it implicit in an `ImportError`.

- **CRLF on non-POSIX checkouts.** Four `write_text` calls emitted platform-native line
  endings and would have failed the golden tests for a reason unrelated to content; all four
  pin `newline="\n"`, and a test scans `src/` so a fifth cannot appear. A second test forbids
  a bare `import fcntl` outside `harness/filelock.py`, since one such import is what made
  `review` and `state show` unimportable in the first place. `assets validate`'s execute-bit
  check is POSIX-gated: Windows never sets that bit, so the check would have flagged every
  hook in a clean checkout — a second, independent break alongside the `fcntl` import.

- The Unreleased section itself said the framework was "not yet on `main`" after PR #1 had
  already merged.
- **D2's transcribed author order was wrong.** `sutton.v2.yaml`'s D2 row cited
  arXiv:2212.10420 as "Bowling, Martin, Dabney, Abel"; the paper's own front matter has
  Bowling and Martin as equal-contribution first authors, then Abel, then Dabney — Dabney
  and Abel were transposed. Found by independently fetching the paper during planning, not
  deferred to a future rebaseline run; it's a live instance of the exact defect class this
  oracle's own header comment exists to catch. Source now marked `verified: true`. A new
  `TestStalenessCap` round-trip test proves this class of fix actually changes downstream
  severity capping (a `verified: false → true` transition), not just the loader's shape —
  nothing exercised that integration path before.

### Security

- Four of the boundary controls DEC-F9 claims did not hold as written, and all four are on
  the untrusted-artifact surface: evidence of retrieval could be minted from a string the
  artifact's author chose, the tool-scoping hook allowed anything it did not recognise and
  never looked at a `Glob` pattern, the SSRF filter passed four spellings of loopback plus
  the carrier-grade NAT range and never checked the URL scheme, and model prose could inject
  markdown structure into the published report. Each is itemised under **Fixed** with what it
  cost. The corrected text is in DEC-F12, DEC-F15 and DEC-F16 and in `docs/architecture.md`
  §8, which previously asserted the opposite of what the code did.

- Those fixes were then attacked in turn, and four of them did not hold either — a forged
  Blocker row in the published findings table, unlaundered `scope_items`, a glob check that
  only caught patterns with a literal first segment, and an honesty check the model could
  opt out of by omitting one optional field. DEC-F19 records what each entry claimed and
  what the code actually did. The lesson is in the entries: a control asserted by a test
  written *to* the implementation is not a control.

- Two file-write primitives on the state path: a symlink planted at the temp or lock path
  gave an arbitrary-file overwrite and a truncate. Fixed under **Fixed** above. The artifact
  read path had the mirror-image gap — a symlink or character device where a document was
  expected — and is now checked too.

- Accepted and documented rather than fixed: `WebSearch` ships in the default tool set with
  an unconstrained query, so an artifact that can steer the model's search terms has an
  outbound channel. DEC-F9 already refuses to credit `WebSearch` results as `fetched`, and
  the content the model holds is the operator's own artifact and oracle, so the exfiltration
  value is low for a single-user reviewer; a multi-tenant deployment must empty
  `unscoped_tools`. Issuance is now logged with the query's length and never its text. A
  `WebFetch` redirect chain from an allowed host to an internal one remains invisible to a
  hook that sees only the initial URL, and a fetch of `doi.org/<id>` is credited without
  inspecting the response body — both need a fetcher the harness does not own. DEC-F11,
  DEC-F12 and DEC-F15 record these as residual risk.

## [PR #1 merge] - 2026-08-22

Everything below merged to `main` via PR #1 (commit `2d4bea6`), from the
`claude/implementation-plan-agent-framework-dbqgvv` branch. `main` previously contained only
the M0 bootstrap commit. No package version has been tagged yet — this section will be
renamed to a dated release when one is.

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

- **The test suite no longer writes into the repository.** `review_log_dir` defaults to
  `docs/review-log` resolved against the working directory, which under pytest is the
  checkout, so integration tests invoking `review` wrote real state, audit bundles, and
  a lock file into the developer's tree and appended a cycle on every run. Fourteen
  cycles of it had been committed. An autouse fixture now redirects review state to a
  temporary directory, four tests guard the mechanism and the outcome, and `.gitignore`
  excludes the whole per-cycle bundle directory rather than only `transcript.jsonl` —
  which is what its comment already claimed.

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
- **Secret scan fixed on pull requests.** The gitleaks action exits non-zero *before
  scanning* without `GITHUB_TOKEN` on `pull_request` events, so the job passed on every
  push and failed on the first PR with something that reads as a leak. Tests now assert
  the token is present, that every action is pinned to a commit SHA, and that no job
  calls a `make` target without installing the environment first.
- **Container test stage repaired.** It ran a hand-copied subset of the gate (no
  format check, layering, oracle or asset validation) over a build context missing
  every configuration file the suite audits — so `make docker-test` passed vacuously
  on the checks that read them. The stage now copies those inputs and invokes
  `make gate`, and static tests assert both, since Docker is not available everywhere
  the suite runs.

### Security

- Reviewed artifacts are treated as untrusted input throughout: tool scoping, a
  data-driven fetch allowlist with internal-host filtering, artifact content delimited as
  data in prompts, output laundering for model prose, and tool-honesty verification
  matched against actual successful tool results rather than the model's claims.
- Secret scanning in CI (full history) and pre-commit; the container runs unprivileged and
  ships no credentials.

[Unreleased]: https://github.com/ianshank/Creative-agent-/compare/2d4bea6...HEAD
[PR #1 merge]: https://github.com/ianshank/Creative-agent-/commit/2d4bea6
