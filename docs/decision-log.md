# Decision Log

Framework decisions for the `creative-agent` harness. Convention: each entry has an ID
(`DEC-F<n>` for framework decisions), a status (`CONFIRMED`, `DEFERRED`, or `PENDING`), the
decision, and its rationale. Per the CONFIRM-FIRST discipline inherited from the sutton-review
spec, mechanisms are implemented only after their governing decision is logged here.

Note on scope: `DEC-S1`..`DEC-S6` from the sutton-review spec are **not** framework decisions.
They are obligations of *reviewed artifacts* that claim Alberta-Plan/OaK conformance; the
harness checks for them in the reviewed repository's own decision log (see `DecisionGate`),
and they never gate this repository's build.

---

## DEC-F1 — Agent registry style — CONFIRMED

Explicit in-process registry (`AgentRegistry` with a dict and `register()`), not
entry-points discovery. Entry points buy cross-distribution discovery this single-distribution
repo does not need, at the cost of editable-install staleness and monkeypatched tests. An
entry-point *loader* can be added alongside the dict when a second distribution actually
exists; migration cost is one function.

## DEC-F2 — Oracle format — CONFIRMED

Doctrine tables are YAML data files validated by pydantic (`schema_version` field, loader
migration policy). All normative content — rows, tiers, severity caps, gates, source-quality
rules, conformance markers, decision traps, protocol thresholds — lives in the data file. A
new research corpus is a new YAML file, never new code. Loaded with `yaml.safe_load` only,
bounded size/depth.

## DEC-F3 — Severity model — CONFIRMED

`Severity` is an ordered enum (BLOCKER > MAJOR > MINOR > INFO). Caps apply by `min()` per
capping rule, **except** blocker legitimacy which uses multi-support semantics: a Blocker
whose doctrine support is all T/E-tier survives only when its `supports` also include a
PR/AP-tier row, a gate failure, a safety failure, or an internal contradiction ("may not
carry a Blocker alone" — the spec's word "alone" licenses corroborated Blockers).
`original_severity` and `cap_reason` are always preserved for audit.

## DEC-F4 — State format — CONFIRMED

Per-artifact review state is a markdown file at `docs/review-log/<artifact-id>.md` with
machine-truth YAML front matter (`schema_version: 1`, cycle history including finding keys
and dispositions) followed by a human-readable summary. Writes are atomic
(tmp file + rename); single-writer per state file is assumed and documented. Old
`schema_version` fixtures are kept frozen in the test suite forever (backwards-compatibility
gate).

## DEC-F5 — LLM structured output — CONFIRMED

Native Claude Agent SDK structured output (`ClaudeAgentOptions.output_format` with a JSON
schema generated from `model_json_schema()`), not fenced-JSON prompting. On
`error_max_structured_output_retries` the call fails typed (`LLMOutputError`); repair is
per-call and bounded by `max_regeneration_attempts`. Repair prompts may only address
verification-log defects, never alter finding severities.

## DEC-F6 — Conformance-mode policy — CONFIRMED

Fail-closed: a review runs in `conformance` mode only when the classify call returns a quoted
conformance claim from the artifact. Markers that suggest a claim ("Alberta Plan", "OaK", …)
live in oracle data (`conformance.markers`); when a marker is present in the artifact but no
quote was returned, the harness re-probes once and, failing that, runs advisory with a mode-
uncertainty flag on the verdict line. Known bias: under-severity on sloppy conformance claims;
accepted per the spec ("non-conformance with a program the author never joined is not a
defect").

## DEC-F7 — Artifact identity — CONFIRMED

Artifact-id resolution order: explicit `--artifact-id` flag → `review-id:` key in the
artifact's front matter → filename slug (separator- and case-normalized). A sha256 content
hash over LF-normalized bytes is recorded in state for drift *warnings* only (content changes
every cycle by design). Builtin `hash()` is banned for identity.

## DEC-F8 — Determinism & coverage policy — CONFIRMED

All time comes from an injected `Clock` protocol (aware-UTC); `datetime.now`/`utcnow`/
`date.today` are lint-banned in `src/`. Coverage gate: branch coverage on, global
`--cov-fail-under=90`, per-package floors checked in CI. `harness/llm/claude_sdk.py` is
visibly omitted from the gate (tested via mocked transport + weekly live run); adding any
other omit or `# pragma: no cover` requires a new decision-log entry.

## DEC-F10 — Observability — CONFIRMED

Structured logging over the `creative_agent` namespace only (never the root logger, so
importing the library cannot reconfigure a host application). Every stage emits a stable,
greppable `event` name with contextual fields; `text` and `json` formats are both
supported and selected by configuration (`log_level`, `log_format`) with `--verbose` /
`--debug` / `--log-format` raising it for one invocation. Prompts and artifact text are
never logged — only sizes, identifiers, counts, and durations. `timed_stage` wraps the
review and each LLM call so a wrong verdict can be traced to the stage that decided it.

## DEC-F9 — Threat model — CONFIRMED

Reviewed artifacts are untrusted input. The SDK session's tools are scoped: `Read`/`Grep`/
`Glob` to the artifact path and oracle directory; `WebFetch` to a domain allowlist derived
from oracle `SourceRef`s plus the artifact's own bibliography; `WebSearch` results support
existence only and never count as `fetched=True`. Artifact content is delimited as data in
prompts. All published text passes through the deterministic renderer; LLM prose fields are
length-capped. Tool honesty is checked against tool **results** (`is_error == False`) with
canonical arXiv/DOI identifier matching, not against tool-use events alone.

**Superseded in part by DEC-F11**: the scoping described above was, until DEC-F11,
enforced only for `WebFetch` (this entry), and not at all for `Read`/`Grep`/`Glob` (see
DEC-F11b) — the wording above should be read as the *intent*, not as a claim that both
halves were enforced from the start.

## DEC-F11 — Real tool-scope enforcement via `PreToolUse` — CONFIRMED

**Problem:** DEC-F9's scoping was computed but not enforced at the SDK call boundary.
`ThreatGuard.fetch_domain_allowlist` reached the model as prose in the system prompt
(advisory only — nothing stopped a `WebFetch` call to an off-allowlist host from actually
executing); `ThreatGuard.allowed_read_roots` was computed and unit-tested but never reached
the SDK or the prompt at all — read-path scoping was fully disconnected, not merely
advisory. Only the downstream tool-honesty check (DEC-F9) would later refuse to credit any
resulting "evidence" — after the call had already run.

**Mechanism:** `ClaudeAgentOptions.hooks["PreToolUse"]` fires for every tool call
unconditionally, regardless of `allowed_tools` or `permission_mode`. The alternative,
`can_use_tool`, only fires when permission evaluation would otherwise prompt — and this
project sets `permission_mode = "dontAsk"` (`config.py`) for the unrelated reason of
running headless, whose entire purpose is to skip that prompt. `can_use_tool` is therefore
very likely dead code in this project's actual runtime mode; `PreToolUse` is the only
mechanism decoupled from a setting already fixed for other reasons.

**Where the decision logic lives:** the path/host predicates (`is_fetch_allowed`,
`is_path_within_roots`) are pure functions in `harness/security.py`, which is fully
coverage-counted. `harness/llm/claude_sdk.py` is the one approved coverage omit (DEC-F8) —
if the predicate logic lived inline in the hook body there, a broken check could ship with
zero measured coverage. `claude_sdk.py`'s hook is thin glue only: extract the tool call's
URL/path from the SDK's `input_data`, call the `security.py` predicate, translate the
boolean into the hook's `hookSpecificOutput` dict.

**Sub-items, shipped separately by severity:**
- **DEC-F11a (this entry, WebFetch):** `is_fetch_allowed(url, allowlist)` checks the
  request's host against the exact `fetch_domain_allowlist` computed for that review call —
  not a fresh `is_internal_host` check, which would incorrectly permit any public host, not
  only the oracle+artifact-derived set the model was told about.
- **DEC-F11b (Read/Grep/Glob, follow-up):** `is_path_within_roots(path, roots)` resolves
  the target path (`Path.resolve()`) and compares with `Path.is_relative_to()`, not string
  prefix matching, so a symlink inside an allowed root pointing outside it is caught rather
  than defeating a naive check.

**Accepted residual risk, not solved here:** a `WebFetch` redirect chain from an allowed
host to an internal one is invisible to a hook that only sees the initial URL. Fixing this
means replacing `WebFetch` with a custom redirect-validating fetcher — over-engineering for
a single-user offline document reviewer, not a multi-tenant service. Documented, not built.

**Verification:** unit tests for the new predicates in `security.py` (pure functions, no
SDK needed); mocked-transport integration tests in `test_claude_sdk_adapter.py` proving a
denial is actually returned for an off-allowlist/out-of-root call. Final confirmation
against the live SDK needs `ANTHROPIC_API_KEY` (roadmap item 1, owner-blocked) but neither
implementation nor these tests do.

## DEC-F12 — Tool-honesty evidence must be authority-bound — CONFIRMED

**Problem:** `VerificationLogChecker._evidence_identifiers` canonicalized the *requested
target string* of a tool call, never the fetched resource, and `canonical.canonicalize`
matches an arXiv id or DOI as a substring anywhere in that string. Three different targets
therefore credited `fetched=True` for the same paper: the genuine
`https://arxiv.org/abs/2401.12345`, a decoy `https://attacker.example/x?src=arxiv.org/abs/
2401.12345` on any host the artifact's own bibliography put on the allowlist, and a local
`Read` of `refs/arxiv.org/abs/2401.12345.md` inside the artifact repository, which is a
read root whose file names the artifact's author controls. The local variant needs no
network at all. `docs/architecture.md` §8 calls this control "the control that makes the
verification log worth reading" and asserts canonical matching means URL noise "neither
defeats nor fake-satisfies" it; canonicalization is exactly what made it fake-satisfiable.

**Decision:** two rules, both deterministic.

1. **Authority binding.** A canonical identifier enters the observed-evidence set only from
   a successful fetch-class result whose target is an `http`/`https` URL *and* whose host is
   an authority for that identifier's scheme. Authorities are configuration, not literals:
   `HarnessSettings.identifier_authority_hosts` maps a scheme prefix to its hosts
   (`arxiv` → `arxiv.org`, `doi` → `doi.org`), matched on the host or any subdomain of it,
   so an institutional mirror or a DOI proxy is a settings change rather than a code change.
   A non-URL target — a local path — contributes no canonical identifier.
2. **Claims are matched by kind.** An entry that names a `canonical_id` is a scholarly
   claim and must be matched by identifier. Raw-string equality against the observed target
   cannot satisfy it, which is what let a local `Read` back a claim about a paper. An entry
   with no `canonical_id` is not a scholarly claim and may still be satisfied by an exact
   target match, which keeps a legitimate `Read` of the artifact under review working.

**Why not tighten `canonicalize` instead:** it is used for identity bucketing in the
source-quality checker and the rebaseline resolver, where a permissive substring match over
a citation string is correct. The defect is not extraction, it is trusting extraction from
an attacker-chosen string as evidence of retrieval. The fix belongs at the evidence
boundary.

**Accepted residual risk:** a fetch of `doi.org/<id>` is credited without inspecting the
response body, so a redirect chain that ends somewhere unexpected still counts. This is the
same residual DEC-F11 documents and declines for the same reason. Requiring the identifier
to appear in the response body would need a fetcher the harness does not own.

**Consequence for reviewers:** a DOI claim is now only creditable from a `doi.org` fetch,
not from the publisher page a DOI redirects to. That is deliberately strict — the failure
direction is a refused review (exit 3), never a published false claim.

## DEC-F13 — An unverified doctrine row is stale from the start — CONFIRMED

**Problem:** `OracleRow.is_stale` returned
`freshness.rebaseline_count >= freshness.max_rebaselines_without_verification`, and the
shipped oracle carries `rebaseline_count: 0` against a threshold of `2`. `0 >= 2` is False
for every row, so no row was ever stale and `severity_policy.unverified_row_cap` never
applied. Six rows with no verified source and a tier in `blocker_tiers` could publish a
full Blocker on a citation nobody had resolved. The trigger was coupled to a counter that
only rises when `oracles rebaseline` runs, and running it is the operation that sets
`verified: true` — so a freshly transcribed oracle, the state in which the protection is
most needed, had none.

**Decision:** the grace budget defaults to **zero**, so a row whose sources are all
unverified is stale immediately and its findings are capped at
`severity_policy.unverified_row_cap`. `max_rebaselines_without_verification` is kept, with
its existing meaning and its existing schema position — an operator who deliberately wants
a grace window raises it and owns that choice. No schema change, no migration, and the
mechanism and its tests are unchanged; only the default and the shipped data move.

**Why the default rather than new logic:** the field already expresses "how much
unverified doctrine this oracle tolerates". The bug was that its default answered "quite a
lot" for a corpus that had never been checked. Fail-closed is the correct default for a
reviewer that refuses to soften its own output.

**Observability:** `OracleLoader` now emits `oracle.unverified_rows` at WARNING when a
loaded oracle contains rows at a `blocker_tiers` tier with no verified source, naming the
row ids and the grace budget in effect. Silence about weak doctrine was how this survived.

## DEC-F14 — Optimistic concurrency for review state — CONFIRMED, supersedes part of DEC-F4

**Problem:** DEC-F4 says single-writer is "assumed and documented". Nothing enforced it.
`FileStateStore.load` took no lock, `save` locked only the tmp-write and `os.replace`, and
the pipeline computed its escalation verdict from the snapshot it had loaded up to 144
provider calls earlier. Two reviews of one artifact both read cycle N, both wrote N+1, and
the second `os.replace` discarded the first's history wholesale. The lost record is the
lesser harm: `CycleEscalator.check` counts recurrences from the stale snapshot, so the
cycle-3 charter-review STOP — which `docs/roadmap.md` itself calls load-bearing — can fail
to fire. It reproduces with two terminals on one machine, where `flock` genuinely
serialises the writes, which is why it looked safe.

**Decision:** detect and fail, do not serialise and do not merge.

`StateStore.save` accepts `expected_cycle: int | None = None`. When given, `save` re-reads
the on-disk state *under the lock it already holds* and raises `StateConflictError` if the
stored cycle no longer matches what the caller loaded. The pipeline passes the cycle it
loaded. The keyword defaults to `None`, so every existing implementation and test double
keeps working unchanged — the protocol widens, it does not break.

**Why not hold the lock across load-to-save:** that serialises concurrent reviews behind a
minutes-long exclusive `flock` with no timeout, which is a hang rather than an error, and
it forces a transaction seam through `StateStore`, the pipeline and every test double.

**Why the run fails rather than retries:** by the time `save` is reached the escalation
verdict, the `ReviewReport` and the rendered markdown are all built from the stale
snapshot. Retrying the write would publish a verdict computed against history that no
longer exists. The honest outcome is to abort without publishing, which is also what the
never-soften rule requires.

`reset()` now takes the same lock, since `--reset-state` raced the writer too.

## DEC-F15 — Deny-by-default tool scoping — CONFIRMED, extends DEC-F11

**Problem:** three gaps in the `PreToolUse` enforcement DEC-F11 introduced.

- `Glob` takes a required `pattern` and an optional `path`. The hook read only the path
  keys and fell back to the session cwd, so `Glob{"pattern": "/etc/**/*"}` and
  `{"pattern": "../../**/*"}` validated the cwd and ignored the pattern. DEC-F11b claims
  `Glob` is covered. It was not.
- The hook returned `{}` (allow) for every tool it did not recognise, and its
  `HookMatcher` only fired for four tool names, so nothing else was ever inspected.
- `tool_input.get(key)` is a truthiness test, so `Read{"file_path": ""}` silently degraded
  to the cwd check and was allowed.

**Decision:** the hook matches every tool and denies by default. A tool is allowed only if
it is explicitly handled: `WebFetch` against the computed host allowlist, `Read`/`Grep`/
`Glob` against the computed read roots *including* their glob patterns, and the tools named
in `HarnessSettings.unscoped_tools` — a settings list, empty of path- and network-bearing
tools by default — for which no target check is meaningful. An unrecognised tool is denied
and logged rather than passed through.

Pattern scoping takes the pattern's longest leading literal segment (the part before any
glob metacharacter), joins it to the call's base directory, resolves it, and applies the
same `is_path_within_roots` predicate. An absolute or traversing pattern is caught; a
plain `*.md` resolves to the base directory and is allowed.

An empty-but-present path key is now a denial rather than a fallback, because a malformed
call is not a safe call.

**Accepted residual risk, documented not solved:** `WebSearch` remains in the default tool
set and its query is unconstrained, so an artifact that can steer the model's search terms
has an outbound channel. DEC-F9 already refuses to credit WebSearch results as `fetched`.
The content the model holds is the operator's own artifact and oracle, so the exfiltration
value is low for a single-user reviewer; a multi-tenant deployment would have to remove it.
The hook logs `security.websearch_issued` with the query *length* and never its text, per
DEC-F10's rule that prompts and artifact text are never logged.

## DEC-F16 — Prose laundering covers line and format characters — CONFIRMED, extends DEC-F9

**Problem:** `launder_prose` stripped `[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]`, which lets `\n`,
`\r`, `\t`, zero-width and bidi characters through, and `_md_escape` was applied only
inside table cells and never to `\r`. Model prose therefore reached the report able to open
a second `**VERDICT**` line, forge a `## Findings / No findings.` section, and corrupt a
verification row. `scope_items` were not laundered at all. The deterministic renderer is
the stated guarantee that the model never emits the report; indirectly, it did.

**Decision:** `launder_prose` additionally folds every line break and tab to a single
space, removes Unicode format characters (category `Cf`, which covers zero-width spaces,
bidi overrides and isolates, and the byte-order mark), maps non-breaking spaces to spaces,
and collapses runs of whitespace. The renderer escapes every model-supplied field it emits,
not only table cells, and the pipeline launders `scope_items` like every other prose block.
Laundering is idempotent, so a value that passes through twice is unchanged.

Structural fields the harness assigns itself — `finding_id`, oracle row ids — are not model
input and are left alone; that is verified by a test rather than assumed.

## DEC-F17 — Run-level budget and timeout, and a retryable abort code — CONFIRMED

**Problem:** `llm_timeout_seconds` was declared in settings and documented in
`config/settings.example.yaml` and used nowhere in `src/` — dead configuration.
`max_budget_usd` was passed to `ClaudeAgentOptions` per call, so the settings file's
description of it as "cap a review's spend" was wrong by the number of calls a review
makes: 18 on the happy path, and up to roughly 144 provider calls once the classify
re-probe, the repair loop and `_call`'s own schema-retry loop compound. `RawLLMResult.
cost_usd` was logged and stored per call and never summed against anything.

**Decision:** the pipeline owns run-level enforcement, not the SDK adapter.

- Cost accumulates across every attempt in `sweep.calls`. The check runs *inside* `_call`'s
  attempt loop, before each provider call, so a single logical call cannot burn several
  times the remaining budget. Exhaustion raises `BudgetExceededError`.
- Each provider call is wrapped in `asyncio.timeout(llm_timeout_seconds)`, making the
  setting live. Expiry raises `LLMTimeoutError`.
- Both are `RunAbortedError`, a new base carrying a new `ExitCode.RUN_ABORTED = 6`: the run
  stopped before producing a verdict, nothing was published, and a retry is meaningful.
  `StateConflictError` (DEC-F14) shares it for the same reason.

**Why a new exit code rather than reusing 3:** `REVIEW_FAILED` means the review ran and
its verification log could not be completed — a statement about the artifact. An abort is a
statement about the run, and conflating them would tell a CI consumer to treat a transient
budget stop as a finding about the document. The exit-code table is a frozen contract, so
this is a deliberate, versioned addition recorded here, in `README.md` and in
`docs/architecture.md`.

**Why enforcement lives in `pipeline.py`:** `harness/llm/claude_sdk.py` is the one approved
coverage omit (DEC-F8), so a budget check written there would ship unmeasured, exactly the
reasoning DEC-F11 used for its predicates.

## DEC-F18 — Migration seams for the two durable read formats — CONFIRMED

**Problem:** every durable format is versioned and both loaders reject an unknown version
outright, so there is no seam at which a `schema_version: 2` file could be upgraded on
read. Adding one retroactively is harder than leaving it now.

**Decision:** one shared, reusable helper (`harness/migrations.py`) applies an ordered
chain of dict-to-dict upgrade steps between the version read and `model_validate`, and both
`harness/oracle.py` and `harness/state.py` route through it. The chain is empty today: v1
is current, so the helper is an identity pass whose plumbing is tested, and the first real
migration adds one registered step rather than a new mechanism.

Because `models/base.SchemaModel` sets `extra="forbid"`, a migration step must operate on
raw dicts and emit exactly the current field set. Each future step therefore needs a frozen
fixture of the version it upgrades *from*; this entry adds the missing frozen v1 oracle
fixture alongside the existing `tests/fixtures/state/v1-example.md`, since
`tests/factories.py` builds against the live model and `CLAUDE.md` forbids using the
shipped product data for engine behaviour.

**Explicitly excluded: the report contract.** `REPORT_CONTRACT_VERSION` is rendered into
the markdown and serialised into `--output-json`, and nothing anywhere parses a report
back. A migration chain for a format with no read side would be ceremony. What that
contract needs is a documented consumer promise, which `README.md` now carries.

## DEC-F19 — Corrections to F12, F15, F16 and F17 after adversarial review — CONFIRMED

DEC-F12 through DEC-F18 were implemented and then attacked by an independent review. Four
of the entries asserted more than the code delivered. A decision log that overstates is
worse than none, for the same reason those entries give about a validator that passes on
nothing, so the corrections are recorded here rather than by editing the originals — what
was believed at the time is part of the record.

**F16 was false about the findings table.** "The renderer escapes every model-supplied
field it emits" — it did not escape `doctrine_refs` or `gate_refs`, which are joined and
interpolated raw into the Doctrine/gate cell. `gate_refs` is copied verbatim from the
model's `CandidateFinding` and is an unconstrained `list[str]`, so a single entry closed
the cell and opened a forged **Blocker** row in the published report: the exact defect the
entry exists to close, in the exact table it names, surviving the fix that named it. Both
ref lists now pass through `_md_escape`, and the guard is a test that counts data rows
rather than one that greps for an escape sequence.

**F16 was also false about `scope_items`.** "The pipeline launders `scope_items` like every
other prose block" — nothing laundered them; only the renderer's pipe-and-newline escape
ran, so a bidi override or a zero-width run reached the report and `max_prose_chars` never
applied. They now pass through `ThreatGuard.launder_prose` with the rest.

**F15's Glob claim held only for patterns whose first segment is literal.**
`glob_pattern_root` trimmed the partial final segment with `rsplit("/", 1)[0]`, which
returns `""` when the first metacharacter falls inside the first segment — and `""` reads
as "relative to the base directory", which is allowed. `/etc*/*`, `/h*me/user/.ssh/*` and
`/*` were all permitted; only `/etc/**/*` was caught. A one-character change walked past
the check. The trailing separator is now kept, so an absolute pattern stays absolute, and
brace or bracket groups containing a path separator, and leading `~`, are refused outright
rather than guessed at. The original tests all used a literal first segment: they were
written to the implementation rather than against it, and the replacements lead with the
escapes.

**F12 was opt-out.** The entry says an entry naming a `canonical_id` is judged by
identifier and one without makes no scholarly claim. But `canonical_id` is optional and the
model writes the entry, so the party the control constrains chose which branch judged it:
omitting the field restored the original local-`Read` forgery verbatim, and a
`canonical_id` that does not canonicalize fell through the same way. A source that carries
a scholarly identifier is now judged by identifier however the entry is shaped, and an
evidence target that canonicalizes can no longer be credited as plain-string evidence.

**F12's authority binding constrained the host, not the retrieval.** `arxiv.org` and
`doi.org` are on every computed allowlist unconditionally and both return 200 for arbitrary
paths, so the decoy simply moved to the registrar: `arxiv.org/?x=arxiv.org/abs/<id>` was
credited. Only the host and path are considered now; a query string or fragment is the
model's choice of string, not evidence of what was served.

**F12 would have failed legitimate reviews.** Requiring the host to be an authority for the
scheme *canonicalization happened to pick* refused `doi.org/10.48550/arXiv.<id>` — arXiv's
own DOI prefix, and the standard modern citation form — because canonicalization prefers
arXiv and `doi.org` is not an arXiv authority. A conformance review citing a preprint
through the DOI resolver the entry explicitly blesses would have exited 3. A port also
defeated the match. The host must now be a registrar for *some* configured scheme;
cross-registrar is deliberate, because a DOI registrar legitimately serves arXiv
identifiers.

**F17 understated the budget overshoot.** "A single logical call cannot burn several times
the remaining budget" was true of the retry loop and false of the run: a pre-call check
cannot know what the call will cost, so a $2 budget with $1.90 calls spent $3.80. Worse,
the adapter still passed the raw setting to the SDK as a *per call* cap, so one number
meant two different things and the practical bound was roughly twice the setting. The
remaining run budget is now carried on `AssembledPrompt` and used as the backend's per-call
ceiling, which bounds the overshoot at the budget itself.

**Known and accepted, not fixed here.** `identifier_authority_hosts` reaches the honesty
checker but not `ThreatGuard.fetch_domain_allowlist`, so configuring a mirror makes the
checker accept a fetch the `PreToolUse` hook will deny; F12's "a mirror is a settings
change" is therefore not yet true end to end, and is recorded in `docs/roadmap.md` rather
than half-fixed. Deny-by-default has no live verification, because that needs the API key
Tranche 0 is blocked on. `_EVIDENCE_SCHEMES` and `ALLOWED_FETCH_SCHEMES` are two literals
for one policy.
