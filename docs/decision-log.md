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
