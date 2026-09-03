# Development plan

What is wrong, what is deliberately not built yet, and what unblocks the most. Ordered by
sequence, not by wish. Items marked **blocked** need something outside the codebase.

This file was rewritten on 2026-09-02 after a peer review that re-verified every claim the
previous version made against the code. Four of its statements were wrong and two of its
priorities were inverted; the corrections are recorded inline rather than quietly dropped,
because a roadmap that was not re-read after the code changed is the same defect class this
project exists to catch. Every finding below carries a `file:line` and was confirmed by
reading the code or by running it. Where a control was attacked and held, that is recorded
too — negative results are the reason to trust the positive ones.

The organising judgement: **this repository's gates are in better shape than its
guarantees.** `make gate` is genuinely green — 470 tests, 95.7% branch coverage, layering,
data and asset validation all enforced — and that is not in question. What the review found
is that several mechanisms the product's correctness rests on do not fire. The tool-honesty
check can be satisfied by a fetch that never happened. A severity cap never triggers. An
escalation can be erased by a second concurrent run. A mutation gate passes on zero mutants,
and a live-SDK job has never executed a test. Building new capability on that signal is
building on a green that has not been earning itself.

## Status, 2026-09-03

Tranches 1 through 4 have landed. Each item below keeps its problem statement — a roadmap
that deletes what it fixed cannot be audited — and carries a **Done** note naming the
decision entry that governs it, or a **Still open** note saying precisely what is left.
Nothing here was marked done without re-reading the code.

An independent adversarial review then attacked the implementation and found that four of
the seven claimed fixes did not achieve what their decision entries asserted, including a
forged Blocker row reaching the published report through the one field the renderer pass
missed. Those corrections are DEC-F19; the entries they correct were left standing, because
what was believed at the time is part of the record.

What remains: **0.1** and **0.2** (both owner-blocked), the parts of **3.1** that need
network egress, the review-time citation check in **3.3**, **4.2's** full Windows support,
the end-to-end half of configurable identifier authorities (see DEC-F19), and all of
**Tranche 5**.

---

## Tranche 0 — Owner actions. Nothing downstream is real until these land.

Both are settings changes, not engineering.

### 0.1 Set the default branch to `main` — **blocked on the owner**

GitHub's default branch is still `claude/implementation-plan-agent-framework-dbqgvv`. The
costs are already visible:

- CodeRabbit skipped PR #7 entirely and said why: *"Auto reviews are disabled on
  base/target branches other than the default branch."* Every pull request so far has gone
  unreviewed by the tool configured to review them.
- `main` (011081b) and the old default branch have diverged. Four merged Dependabot bumps,
  PRs #3 through #6, are not on `main`; two more, #2 and #8, are open against the wrong
  base.
- Both scheduled workflows run against the feature branch, so the weekly signal does not
  describe `main`.

Fix in Settings → General, retarget the open Dependabot pull requests, reconcile the two
branches, and add branch protection on `main` requiring CI in the same pass.

### 0.2 Add `ANTHROPIC_API_KEY` to repository secrets — **blocked on the owner**

Nothing has ever run against the real Claude Agent SDK. See 2.2 for what the weekly
workflow does in the meantime, which is worse than not running at all.

---

## Tranche 1 — Guarantees that do not hold.

Findings where the code does not do what the decision log and architecture document say it
does. All were reproduced. These come before everything else because the rest of the plan
assumes them.

### 1.1 The tool-honesty check can be satisfied by a fetch that never happened

`harness/verification.py:45-53` canonicalizes the **requested target string**, never the
fetched resource, and `harness/canonical.py:43-48` matches an arXiv or DOI identifier as a
substring anywhere in that string. Reproduced:

```
'arxiv:2401.12345'  <- https://attacker.example/x?src=arxiv.org/abs/2401.12345
'arxiv:2401.12345'  <- /repo/refs/arxiv.org/abs/2401.12345.md
'arxiv:2401.12345'  <- https://arxiv.org/abs/2401.12345
```

All three credit `fetched=True` for the same paper. The first needs only that the
attacker's host is on the fetch allowlist, which the untrusted artifact's own bibliography
puts there by design. The second needs no network at all: the artifact repository is a read
root and its author controls the file names, so a file at `refs/arxiv.org/abs/<id>.md`
mints evidence for `<id>`. Nothing checks that the fetched host is the identifier's
authority, that the response contains the identifier, or that `source_url` is a URL at all
(`models/verification.py:25` is a bare `str`).

`docs/architecture.md` §8 calls this "the control that makes the verification log worth
reading" and says canonicalization means version suffixes and redirects "neither defeat nor
fake-satisfy the check". Canonicalization is precisely what makes it fake-satisfiable.
`tests/unit/test_verification.py:58` blesses the raw-target match; no test uses a decoy.

**Do:** require the identifier to appear in a resolvable position of a fetched URL from the
identifier's own authority host, not anywhere in an arbitrary string, and refuse to credit a
local `Read` as a fetch for a remote identifier. Needs a decision-log entry: this changes
what counts as evidence, which is the heart of DEC-F9.

**Done — DEC-F12.** `canonical.fetched_identifier` credits an identifier only from an
`http`/`https` fetch whose host is an authority for that identifier's scheme (host or
dot-anchored subdomain, so `notarxiv.org` fails and `export.arxiv.org` passes), and
`VerificationLogChecker.check_tool_honesty` matches a `canonical_id` entry by identifier
only — a raw-target match can no longer back a scholarly claim, while an entry making no
such claim still accepts one, so reading the artifact under review keeps working.
Authorities are `HarnessSettings.identifier_authority_hosts`, threaded from settings at the
composition root, so a mirror or DOI proxy is configuration. `canonicalize` is deliberately
unchanged: it is right for identity bucketing, and the defect was trusting its output as
proof of retrieval. Decoy-URL and local-`Read` cases are now tests rather than a blessing.

### 1.2 The staleness severity cap never fires

`models/oracle.py:108-114` returns
`freshness.rebaseline_count >= freshness.max_rebaselines_without_verification`, and
`sutton.v2.yaml` carries `rebaseline_count: 0` against a threshold of `2`. `0 >= 2` is
False for every row, so **no row is stale and the cap is inert.**

The previous roadmap had this backwards. It said the unverified rows mean "the oracle is
currently weaker than it looks" because the staleness rule caps them at Minor. The opposite
holds: unverified rows are uncapped, so a finding whose only support is a transcribed
citation nobody has resolved can publish as a full Blocker with exit code 2. Six rows have
no verified source and a tier that permits a Blocker — D4, D5 and D11 at AP, D8, D9 and D10
at PR. D1 and D3 at tier E and D12 at tier T are separately held to Major by `tier_caps`.

The trigger is coupled to the wrong counter. `rebaseline_count` rises only when the
rebaseline command runs, and running it is the operation that sets `verified: true`. A
freshly transcribed oracle that has never been rebaselined gets no protection, which is
exactly the state in which it is needed.

**Do:** cap an unverified source from the start, and let the rebaseline budget govern how
long a *previously verified* source stays trusted. That reading matches the oracle header's
own warning that transcription without resolution is the defect class the file exists to
catch. Needs a decision-log entry, since it changes published severities, and a test that
fails on the inert case.

**Done — DEC-F13.** The grace budget defaults to zero, so a row with no verified source is
stale from the first review and its findings are capped at
`severity_policy.unverified_row_cap`. `max_rebaselines_without_verification` keeps its
meaning, its schema position and its mechanism, so an operator who wants a grace window
raises it and owns that; no migration was needed. `sutton.v2.yaml` sets it to `0` with the
reason inline. `OracleLoader` now emits `oracle.unverified_rows_uncapped` at WARNING when a
corpus combines blocker-tier unverified rows with a nonzero budget, which is the
configuration that let this survive silently.

### 1.3 A concurrent review can erase the charter-review escalation

The lock covers the write only, and the read happens minutes and up to 144 provider calls
earlier.

- `harness/state.py:54` — `load` takes no lock.
- `harness/state.py:83-91` — `save` takes `_locked` around a tmp-write and `os.replace`.
  This is the only call site of `_locked` in the file.
- `harness/pipeline.py:316` — state is loaded, unlocked.
- `harness/pipeline.py:542` — the escalation verdict is computed from that snapshot.
- `harness/pipeline.py:601` — the new state replaces the file wholesale.

Two reviews of one artifact both read cycle N, both compute N+1, and the second
`os.replace` discards the first one's history. The lost record is the small consequence.
The real one is that `CycleEscalator.check` counts recurrences from the stale snapshot, so
a Major that recurred in the concurrent run is invisible and the cycle-3 charter-review
STOP can fail to fire. That STOP is the mechanism this file's own "Explicitly not planned"
section calls load-bearing.

The previous roadmap framed this as "two reviews on different machines, or state on a
branch that is later rebased", and as a documentation task. Both are wrong. It fires with
two terminals on one machine — `flock` genuinely serialises the writes there, which is why
it looks safe — and `CLAUDE.md` advertises `/loop 30m`, making concurrent invocation a
documented workflow. `docs/architecture.md:384` is more accurate than the roadmap was: it
says the lock is around the *write*.

**Do:** optimistic concurrency, not a longer lock. Holding the lock across load-to-save
serialises reviews behind a minutes-long exclusive `flock` with no timeout, which is a hang
rather than an error. Have `save` carry the expected prior cycle, re-read under the lock it
already holds, and raise a typed `StateConflictError` on mismatch. The run must then fail
rather than retry: by that point the escalation verdict, the report and the rendered
markdown are all built from the stale snapshot, so the verdict is already wrong. `reset()`
at `state.py:107` is unlocked and races with `--reset-state` too. DEC-F4 states single-writer
is assumed, so this needs a superseding entry. No test exercises the lock.

**Done — DEC-F14, superseding part of DEC-F4.** `StateStore.save` takes
`expected_cycle: int | None = None` and `FileStateStore` re-reads the stored cycle under the
lock it already holds, raising `StateConflictError` on a mismatch; the pipeline passes the
cycle it loaded. The keyword default keeps every existing implementation and test double
working — the protocol widened rather than broke. The run aborts (exit 6) rather than
retrying, because by `save` the verdict, report and rendered markdown are already built from
the stale snapshot. State that has become unreadable at that point is reported as a conflict
rather than a `StateCorruptError`, so a concurrent writer's partial file is not
misattributed to this run. `reset()` takes the same lock. The lock is now exercised by
`tests/unit/test_filelock.py` and the conflict path by `tests/unit/test_state.py`.

### 1.4 `Glob` patterns are unchecked, and the hook is fail-open outside four tools

`harness/llm/claude_sdk.py:35,80` reads the target from `("file_path", "path")` and falls
back to the session cwd. `Glob` takes a required `pattern` and an optional `path`, so with
`path` absent the hook validates only the cwd, which is inside a root, and ignores the
pattern. Driving the wired hook with roots `[/tmp/root]`: `Glob{"pattern": "/etc/**/*"}`,
`{"pattern": "/home/user/**/*.pem"}` and `{"pattern": "../../**/*"}` are all allowed.
`_TARGET_KEYS` at `claude_sdk.py:33` already includes `"pattern"` for evidence extraction,
so the module knows the pattern is a target; the hook simply does not consult it. The effect
is path enumeration outside the roots, not content — a subsequent `Read` of a discovered
path is correctly denied. DEC-F11b claims `Glob` is covered.

Separately, `_pre_tool_use_hook` ends in `return {}`, which allows, and the
`HookMatcher(matcher="WebFetch|Read|Grep|Glob")` means it is not invoked for anything else.
`agent_tools` defaults to `["Read","Grep","Glob","WebFetch","WebSearch"]`, so `WebSearch`
ships granted with an entirely unconstrained query. DEC-F9 constrains how WebSearch
*results* are credited and never what leaves in the query, which is an unmonitored outbound
channel for an artifact that can steer the model's search terms.
`test_claude_sdk_adapter.py:186` enshrines the fail-open behaviour as intended.

**Do:** validate `pattern` as a path target; decide explicitly whether the hook denies by
default; and either drop `WebSearch` from the default tool set or state in DEC-F9 why an
unconstrained query is acceptable. Also handle a `null` `tool_input` at `claude_sdk.py:65`,
which currently raises inside the hook.

**Done — DEC-F15.** The `HookMatcher` carries no matcher, so the hook sees every tool, and
it denies by default: a call is allowed only when explicitly scoped or when its tool is in
the new `unscoped_tools` setting. `Glob`'s `pattern` is scoped as a path via
`security.glob_pattern_root` plus the existing containment predicate, and per-tool argument
shapes are data (`_TOOL_SCOPES`) so `Grep`'s regex is not mistaken for a path while its
`glob` is scoped. A present-but-empty path key is a denial. A null or non-mapping
`tool_input` is normalised to an empty mapping and judged on its merits, since whether the
SDK reads a hook exception as allow or deny is not something to depend on. `WebSearch` was
kept in the default set with its residual risk written down rather than dropped, and now
logs `security.websearch_issued` with the query's length and never its text; a multi-tenant
deployment must empty `unscoped_tools`. The test that enshrined the fail-open behaviour is
replaced by `TestPreToolUseClosedHoles`.

### 1.5 The internal-host filter misses non-dotted-quad IPv4 literals

`ipaddress.ip_address()` rejects short, hex and octal forms, so they fall through to the
name branch, which only asks whether a dot is present. Reproduced:

| host | `is_internal_host` | resolves to |
|---|---|---|
| `127.1` | False | 127.0.0.1 |
| `127.0.1` | False | 127.0.0.1 |
| `0x7f.0.0.1` | False | 127.0.0.1 |
| `0177.0.0.1` | False | 127.0.0.1 |
| `100.64.0.1` | False | CGNAT, RFC 6598 |

`http://127.1/admin` in an artifact bibliography joins `fetch_domain_allowlist`,
`is_fetch_allowed` permits it, and the fetcher's resolver expands it to loopback.
`rejected_fetch_hosts` reports nothing, so the audit log is silent. This contradicts
`docs/architecture.md` §8 directly. `tests/unit/test_security.py:52-66` parametrizes only
canonical forms.

In the same function, `is_fetch_allowed` validates the host and never the scheme:
`file://arxiv.org/etc/passwd` returns True, and `arxiv.org` and `doi.org` are on every
allowlist unconditionally, so this needs no artifact cooperation.

**Do:** normalise through `socket.inet_aton` or reject any host that is not a valid DNS
name or a canonical IP literal, add RFC 6598 to the reserved set, and require the scheme to
be `http` or `https`.

**Done — DEC-F15.** `security._as_ip_address` normalises through `socket.inet_aton`, the
same parser a real fetcher uses, so the gap is closed at its source rather than by
pattern-matching the four known spellings. Hostnames are unaffected: `inet_aton` rejects
anything with a letter that is not a hex-digit prefix. `not is_global` is checked alongside
the explicit categories, which brings in RFC 6598 — neither predicate is sufficient alone,
since IPv4 multicast is `is_global`. `is_fetch_allowed` now rejects any scheme outside
`{http, https}`. `tests/unit/test_security.py` parametrizes the non-canonical forms.

### 1.6 Model prose injects markdown into the published report

`harness/security.py:22` defines `_CONTROL_CHARS` as `[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]`, so
`\n`, `\r` and `\t` all survive `launder_prose`. `_md_escape` handles `|` and `\n` but is
applied only inside table cells and never to `\r`. Four fields reach the report raw:
`verdict.headline` (`rendering.py:53`), the `what_survives` and `residual_risks` bullets
(`:82`, `:86`), `scope_items` (`:106`, which `pipeline.py:574` does not even launder), and
the model-supplied `row_id` in a table cell (`:35`). A rendering run produced a second
VERDICT line, a forged `## Findings / No findings.` section and a corrupted verification
row, all from prose the untrusted artifact can steer. The deterministic renderer is the
stated guarantee that the model never emits the report; here it does, indirectly.

Zero-width and bidi characters survive too — `U+200B`, `U+202E`, `U+2066`, `U+FEFF`,
`U+00A0` — so a finding can render in reverse of the text stored in state.

**Do:** strip or escape newlines, carriage returns, zero-width and bidi controls in
`launder_prose`, and escape every model-supplied field at the renderer rather than only
table cells. `tests/unit/test_security.py:288` checks only NUL, ESC and the length cap.

**Done — DEC-F16.** `launder_prose` folds line breaks, tabs and the other layout characters
to a single space *before* deleting control characters (so `\v` and `\f` do not splice two
words together), removes Unicode category `Cf`, maps non-breaking spaces, and collapses
whitespace runs; it is idempotent, which matters because prose passes through once per
repair-loop iteration. The renderer escapes every model-supplied field it emits — the verdict
headline, the escalation message, the `what_survives` and `residual_risks` bullets, the
scope-item reference and the model-supplied `row_id` — not just table cells, and `_md_escape`
handles `\r` as well as `\n`. Harness-assigned structural fields are left alone, which a test
asserts rather than assumes.

**Done — DEC-F19.** `ReviewPipeline._laundered_scope` puts `ScopeItem.reference` through
`ThreatGuard.launder_prose` at assembly, so the cap and the format-character strip now reach
`--output-json` as well as the rendered report. DEC-F16's wording was corrected in the same
pass; an independent review found the claim false before the code caught up with it.

### 1.7 Symlink handling on the write and read paths

`harness/state.py:47,88-91` — `tmp_path.write_text` and the lock-file `open` both follow an
existing symlink. Reproduced: a symlink at `<log>/x.md.tmp` pointing outside the directory
is written through, giving an arbitrary-file overwrite with partly attacker-influenced
content, plus a truncate primitive via the lock file. `os.replace` onto the final path is
correctly safe. There is also no `fsync` before the replace, so "atomic" holds for
interleaving but not for a crash.

`harness/artifact.py:36-50` — nothing checks `is_symlink()` or `S_ISREG`. Typer's
`dir_okay=False` rejects directories only, so a symlink at `docs/design.md` inside a
reviewed worktree, which is the documented `--artifact-repo` flow and which git carries, is
read and sent to the model. `path.stat().st_size` reports 0 for a character device, passing
the size cap before an unbounded `read_bytes()`.

**Do:** `O_NOFOLLOW | O_EXCL` on the tmp and lock files, `fsync` before `os.replace`, and a
regular-file check plus a containment check against `--artifact-repo` on the artifact path.

**Done, the write half — DEC-F14.** Locking and atomic writes moved into
`harness/filelock.py`. The tmp path is unlinked and re-created with `O_EXCL` (`unlink`
removes a symlink itself, never its target), the lock opens with `O_NOFOLLOW`, and content is
fsynced before the rename with the directory entry fsynced after. Writing the tests found a
further defect in the original code: the partial-file cleanup was scoped to `except OSError`,
so a non-`OSError` failure mid-write left the fragment on disk — the exact case the earlier
"partial state files" fix was meant to cover. It is a `finally` now.

**Done.** `read_artifact` refuses anything that is not a regular file, which closes the
character-device case where `st_size` reported 0 and the size cap passed before an unbounded
read. It also takes a `containment_root`: a path the operator located *inside* the reviewed
repository must still resolve inside it, so a symlink at `docs/design.md` pointing at
`~/.ssh/id_rsa` is refused. The check is deliberately conditional on where the operator
pointed — naming a document that lives elsewhere while passing `--artifact-repo` so the
repository's own decision log is what `DecisionGate` reads is a legitimate pattern, and the
first cut of this fix broke it.

### Controls that were attacked and held

Worth recording so the fixes above are not read as a general verdict on the boundary.
`validate_artifact_id` resisted every separator, newline, traversal, empty, dot-leading and
control-character input tried, and no input produced a path outside `docs/review-log/`.
`is_path_within_roots` held against a symlink inside a root escaping out, a symlink to
`/etc/passwd`, absolute and relative traversal, prefix-sibling collisions, NUL bytes and
`/proc/self/environ`; `resolve()` plus `is_relative_to()` is the right construction. The
`delimit_artifact` closing-sentinel escape resisted nested splits, partial-prefix splits,
pre-escaped text, zero-width insertion and case variants. Host parsing in `is_fetch_allowed`
correctly denies userinfo (`http://arxiv.org@evil.com/`), fragment (`#arxiv.org`), Cyrillic
homograph, ideographic full stop and trailing-dot forms while normalising case and port.
IPv6, IPv4-mapped and integer literals are all classified correctly. `finding_id` is
harness-assigned, so the unescaped render at `rendering.py:73` is not injectable.

---

## Tranche 2 — Gates that do not gate.

Each reports success while verifying nothing, or verifies less than its documentation
claims. Cheap individually, and they are what makes the rest of the plan trustworthy.

### 2.1 The mutation gate passes on zero mutants

`scripts/check_mutation_baseline.py:28` gates only
`("survived", "no_tests", "suspicious", "timeout")`. `total` and `killed` are printed and
never compared. A run generating no mutants — a rename under `[tool.mutmut].source_paths`,
an `also_copy` breakage, version drift — emits all zeros, every category passes `0 <= 0`,
and the job is green. Verified: feeding the script an all-zero stats file against the real
452/452 baseline exits 0. `scripts/check_coverage_floors.py:88-93` guards the identical
failure and says so; the mutation script's docstring lists the failure modes it must not
have and this is not among them.

**Do:** fail when `total` is 0 or falls below the baseline by more than a declared
tolerance, and add the case to `TestMalformedInputFailsLoudly`.

**Done.** `total` is gated downwards against the baseline with a named tolerance
(`MAX_POPULATION_SHRINK_RATIO`), a collapse to zero fails outright with a message saying why
the categories above it are meaningless, and the docstring now lists the failure mode it
omitted. Covered in `tests/unit/test_mutation_baseline.py`.

### 2.2 The weekly live-SDK job has never run a test

`live.yml` runs `uv run pytest -m live`, and the only live test skips itself when
`ANTHROPIC_API_KEY` is absent. The last run logged `1 skipped, 470 deselected in 1.58s` and
reported success. The workflow opens an issue on failure, so the absence of issues reads as
evidence the SDK surface has not drifted. It is evidence of nothing.

**Do:** fail when the secret is missing rather than skipping, so the false green cannot
recur after the key is added and later rotated. Worth doing before 0.2, not after.

**Done.** `live.yml` gains a pre-flight step that fails the job with a message before pytest
runs when `ANTHROPIC_API_KEY` is absent. The fork guard is unchanged. This is now waiting on
0.2 rather than hiding it.

### 2.3 `ClaudeSDKAdapter.generate()` is verified by nothing

`harness/llm/claude_sdk.py:140-147` is the only place the real `claude_agent_sdk.query` is
bound. Every test, including the `sdk-mocked` leg of the shared contract suite, calls the
private `_run` with a hand-written fake transport. The module is coverage-omitted under
DEC-F8, and the live test skips. Three gates all decline to look at the one function that
touches the SDK.

**Do:** one test that monkeypatches `claude_agent_sdk.query` and calls the public
`generate()`, and one covering the `ImportError` to `LLMTransportError` branch.

**Done.** `TestGenerateIsTheRealEntryPoint` monkeypatches `claude_agent_sdk.query` and
calls the public `generate()`, asserting that the prompt text and the computed options
actually reach the SDK rather than only that the symbol was bound, plus the
`ImportError` to `LLMTransportError` branch that keeps a missing optional extra from
escaping to the CLI as exit 5.

### 2.4 The security tests for DEC-F11 can silently vanish

`tests/unit/test_claude_sdk_adapter.py:128,143,156,209` gate the `PreToolUse` enforcement
tests behind `pytest.importorskip("claude_agent_sdk")`, and `claude-agent-sdk` is an
optional extra. Any environment synced without `--all-extras` drops the whole
sandbox-escape class and reports green. CI is safe today only because `make install`
happens to use `--all-extras`. The tests do run in the locked dev environment, so DEC-F11's
verification claim is honest about existence; 1.4 is about their coverage.

**Do:** hard import, plus one test asserting the `llm` extra is present in the dev sync.

**Done.** Every `importorskip` is gone and the SDK is imported at module scope, so a
missing extra fails loudly at collection instead of quietly deleting the sandbox-escape
tests. `TestTheOptionalExtraIsPresent` states the requirement explicitly rather than leaving
it implicit in an ImportError, and asserts that `make install` still passes
`--all-extras`.

### 2.5 The report contract is asserted only by regenerable goldens

`make goldens` re-runs the suite with `--update-goldens`, and `GoldenComparer.check` writes
instead of comparing under that flag. The `[Unverified — flagged for human check]` marker,
the verification-log table, the Scope section and the `STOP — charter review` block exist in
`tests/golden/report-full.md` and nowhere else. Removing the unverified marker from
`rendering.py` and running `make goldens` leaves the suite green with the central honesty
guarantee gone.

**Do:** assert outside the goldens that each required section and marker is present, so the
goldens guard formatting and unit tests guard existence. Fix
`test_no_environment_leaks_into_goldens` while there: its Windows path pattern `[A-Z]:\\\\`
requires two literal backslashes and is a dead branch.

**Done.** `tests/unit/test_report_contract.py` asserts the marker and every required section
directly, with no reference to the golden files, so the goldens guard formatting and these
guard existence. The Windows-path pattern is fixed and covered by a test that it matches a
real leak.

### 2.6 `assets validate` passes on a gutted `.claude`

`harness/assets.py:187-217` glob-walks `agents/*.md`, `skills/*/SKILL.md` and `hooks/*.sh`,
each guarded by `if ... is_dir() else []`. Renaming `agents/` to `agent/` yields
`0 agent(s), 0 skill(s), 0 hook(s)` and `ok: all assets valid`. The only real guard lives in
the pytest suite, which the `data-validate` CI job does not run.

**Do:** fail on an empty inventory, mirroring the `"no oracle files found"` guard at
`cli.py:110`.

**Done.** The expected inventory is data (`EXPECTED_ASSET_KINDS`, one `AssetKind` per
directory) rather than literals inside `collect`, and an empty result for any kind is a
defect. `settings.json` is named separately for the same reason: the hooks it wires up do not
run without it, and its absence was silent.

### 2.7 The PostToolUse hook does not cover every edit tool

`.claude/settings.json` matches `"Edit|Write"`, missing `MultiEdit` and `NotebookEdit`.
`CLAUDE.md` says the hook re-validates oracle data "after any edit under `data/oracles`".
`TestHookBehaviour` executes the scripts directly by subprocess, bypassing the matcher, so
nothing tests the wiring — the matcher could be `"Bash"` and every test would stay green.

**Do:** widen the matcher and assert it names every edit-capable tool. Related hazard in the
same file: `tests/unit/test_assets.py:189` writes `_hooktest_broken.yaml` into the real
`src/creative_agent/data/oracles/`, and an interrupted run leaves it behind and breaks
`make oracles`, in a suite containing `TestTheSuiteDoesNotMutateTheRepository`.

**Done.** The matcher covers `MultiEdit` and `NotebookEdit`, and a test asserts the wiring
rather than only running the scripts by subprocess — which bypasses the matcher entirely, so
it could have named `Bash` and the suite would have stayed green. The hook test now writes
its broken oracle into a tmp search path instead of the packaged directory.

### 2.8 The container gate is asserted statically and the assertion has a hole

`make docker-test` is in no workflow; the `container` job builds only `target: runtime`. The
static guard that every file the suite reads is copied into the image finds dependencies
with a regex that only sees literals adjacent to `ROOT /`, so the parametrized list at
`tests/unit/test_project_config.py:104` is invisible to it. That list includes `CLAUDE.md`,
and `Dockerfile:53` copies `Makefile CHANGELOG.md Dockerfile .gitignore .dockerignore
.gitleaks.toml` without it. `make docker-test` would fail today on
`test_referenced_documents_exist[CLAUDE.md]`, and the guard built to catch exactly that
passes.

**Do:** add `CLAUDE.md` to the `COPY`, run the `test` stage in CI, and make the scan resolve
parametrize lists rather than adjacent literals.

**Done.** `CLAUDE.md` is copied, the `container` job builds the `test` target and runs the
gate inside it, and the dependency scan resolves parametrized path lists via the AST. Two
tests guard the scan itself — one that it still discovers the known dependencies, one on a
synthetic module that a parametrized path is seen — and a third that it does not
over-collect, since a scan demanding `COPY` lines for strings nobody opens gets deleted.

### 2.9 Smaller gate repairs

- `tests/unit/test_project_config.py:196` skips the leaked-state detector when
  `docs/review-log/` is absent. That directory is tracked only by a `.gitkeep`, so removing
  the placeholder disables the test guarding the regression that already reached a pull
  request once. Assert instead of skipping.
- `pyproject.toml:97` sets `addopts` with no `--strict-markers` and no
  `filterwarnings = ["error"]`. A mistyped `@pytest.mark.live` deselects a test from every
  run with no signal.
- The CI-covers-`make gate` test greps only `run: make <target>`, so any check added as a
  raw step is invisible to the comparison. That blind spot is why 2.8 went unnoticed.

**Done, all three.** The leaked-state detector asserts the directory exists instead of
skipping. `pyproject.toml` sets `--strict-markers` and `filterwarnings = ["error"]`, and the
suite needed no allowances. The CI test now accounts for every step: setup, a `gate`
dependency, or an entry in `CHECKS_OUTSIDE_THE_GATE` carrying its reason — and each declared
exception must match a real step, so a stale entry cannot quietly widen what is accepted.

---

## Tranche 3 — Make the review real.

Tranches 1 and 2 are about trusting the signal. This one is about the product reviewing.

### 3.1 Rebaseline the oracle, from somewhere with network access

Ten of thirteen rows carry `verified: false`. The peer review resolved three by hand against
the primary sources, so the backlog is smaller than the count suggests:

- **arXiv:2208.11173** (D5 and D11), "The Alberta Plan for AI Research" — title page reads
  Richard S. Sutton, Michael Bowling, Patrick M. Pilarski. The YAML matches exactly. D11's
  non-obvious claim also checks out: step 11 is "Prototype-AI III: Oak", spelled "Oak" in
  the paper, which is what the row asserts.
- **arXiv:2202.03466** (D8), "Reward-Respecting Subtasks for Model-Based Reinforcement
  Learning" — title page reads Sutton, Machado, Holland, David Szepesvari, Timbers, Tanner,
  White. The YAML matches exactly, including David Szepesvari, whom the Alberta Plan's own
  bibliography omits.

D9 and D10 were corroborated from the reference lists of those two papers — Sutton, Precup
and Singh in *Artificial Intelligence* 112:181-211, and the seven-author Horde paper at
AAMAS 2011 pages 761-768, both matching the YAML — but neither can be machine-resolved. See
3.2.

**An operational constraint the previous plan missed:** `arxiv.org`, `doi.org` and
`api.crossref.org` are all blocked by the network egress proxy in the Claude Code web
environment. The dry run reports every arXiv row as `unreachable ... 403 Forbidden`, which
reads as an arXiv outage and is not one. "Run the rebaseline locally" is not a viable
instruction here.

**Do:** make `oracles rebaseline` a scheduled workflow job with egress that opens a pull
request with the diff, rather than a manual local step. Ship incrementally. Separately,
`ResolutionResult("unreachable")` is returned for a genuine arXiv error entry, a network
failure and a proxy block alike, so an operator cannot tell "arXiv is down" from "this
identifier does not exist" — give the failure modes distinct detail strings.

**Done, the hand-resolved part.** The three rows above are now `verified: true` with
`last_verified: 2026-09-02` and a note on each source recording how it was resolved and that
`arxiv.org` was unreachable from the review environment. The file header now also states,
per row, which sources are expected to stay unverified forever — a blog post (D1), a book
chapter (D3), an OpenReview workshop paper (D4), an author-hosted PDF (D7's second source and
D10) and an unpublished talk (D12) have no resolvable archival identifier — so the cap is
their correct end state, not a backlog item.

**Still open, and it needs egress.** D9's and D10's identifiers are still untranscribed and
the two rows stay `verified: false`; the scheduled rebaseline job with network access, opening
a pull request with the diff, is not built. The failure-mode detail strings are unchanged: the
arXiv backend already distinguishes a transport error, a malformed feed, an empty feed and an
arXiv error entry, but a proxy 403 still surfaces only as the transport exception's text, so
"blocked here" and "arXiv is down" still read alike. The Crossref backend added in 3.2 has the
same shape and the same limitation.

### 3.2 Add a DOI resolver, and reconsider what `verified` gates

`harness/citations.py:43-45` returns `skipped` whenever `arxiv_id` is absent. There is no
DOI resolver and no URL resolver, so four sources can never be verified by any code path
that exists: D6's Nature DOI, D9's Elsevier DOI, D10's author-hosted URL, and D7's second
source.

Once 1.2 makes the staleness cap fire, this becomes a severity bug rather than a gap. D10
is a genuinely peer-reviewed AAMAS paper whose findings would be permanently capped at
Minor — not because the evidence is weak, but because nobody transcribed a DOI for it.
Severity would be decided by the presence of an identifier string.

**Do:** a Crossref or content-negotiation DOI resolver beside the arXiv one, sharing the
mechanical author diff. That takes verified sources from three to eight. D1, D3, D4, D12 and
the IDBD reference stay unverified, which for a blog post, a book chapter, an OpenReview
workshop paper and an unpublished talk is the correct end state, not a failure. Consider
marking an unresolvable-by-construction source as such in data rather than sharing a flag
with "not yet checked".

**Done.** `CrossrefCitationResolver` resolves DOI-identified sources and
`CompositeCitationResolver` dispatches to whichever backend can identify a source, with
`oracles rebaseline` wiring arXiv first and Crossref second. The Crossref backend mirrors the
arXiv one's contract including both of its hard-won refusals — a source declaring no authors
is `unreachable` rather than auto-verified, and a transport or shape failure is `unreachable`
rather than `mismatch`. The composite reports `skipped` only when every backend skipped, so a
source with no resolvable identifier says so instead of borrowing another backend's failure.
`crossref_api_url` and `citation_user_agent` are settings; the user agent deliberately is not
an operator email.

**Deliberately not done:** the "unresolvable by construction" data flag. It stays a
suggestion. The distinction is currently carried in the oracle file's header prose, which is
weaker than a field but does not add a schema version to a format that has one — revisit it
when a second corpus makes the case.

### 3.3 Make `--offline` honest about what it cannot do

An offline review is structurally incapable of failing on artifact content, and this is
documented nowhere a user would look. Traced and reproduced:

- `OfflineLLMClient` returns `{"claims": []}` for the claims call, so
  `MeasurementGateChecker` — the checker carrying the doctrine's entire quantitative bar and
  the only source of a `compute_budget` Blocker — yields nothing.
- All 13 row dispositions come back `not_applicable`; the judgement and source-quality
  sweeps return empty.
- Offline classify recommends `advisory`, so `auto` mode always resolves to advisory and
  `advisory_severity_cap: info` flattens everything to Info. Exit code 0.
- `protocol.offline_artifact_class: architecture_design` pins the class before any text is
  read, and that class declares no `requires_gates` and no `requires_sections`, so a
  document calling itself a deployment blueprint can never trigger the compute-budget
  Blocker or the missing-safety-section finding offline.

A dogfood run on a document planted with three contradictory symbol definitions, unsupported
performance numbers, a fabricated citation and an unsupported OaK claim found 21 real
defects and exited 0, reporting nothing above Info. Forced to `--mode conformance` the same
run reported 3 Blockers and 15 Majors and exited 2. What it never found: every unsupported
performance claim, including one stating in so many words that no baseline comparison was
included, and the fabricated arXiv identifier. There is no deterministic citation-existence
check in the review path at all — resolution lives only in `oracles rebaseline`.

The deterministic half does work. The same run exercised artifact reading, oracle loading,
prompt assembly, the full call orchestration, severity capping in both directions, findings
assembly and sorting, state persistence with content hashing, recurrence detection, the
cycle-3 charter-review STOP, the exit-code contract and the audit bundle.

**Do:** print an explicit ceiling banner on offline runs saying the run cannot fail, and
document that `--mode conformance` is required for a gating offline run. `make
review-offline` and `make docker-review` are pass-throughs as written, and anyone wiring
`--offline` into CI is running a no-op. Then add a deterministic citation-existence check at
review time, which is the largest fidelity gap that does not need an API key, and which
also closes the fabricated-citation hole 1.1 leaves open from the other side.

**Done, the banner.** `creative-agent review --offline` prints a ceiling banner to **stderr**
— stderr because the rendered report on stdout is a byte-stable published contract — saying
that the run performed deterministic checks only, and, in `auto` mode, that it resolves to
advisory and therefore could not have failed on content, naming `--mode conformance` as the
gating alternative. A test asserts the banner stays out of the `--output-json` payload.
`README.md` carries the same statement where a user reads before wiring `--offline` into CI.

**Still open: the review-time citation-existence check.** Resolution still lives only in
`oracles rebaseline`, so a fabricated identifier in the artifact is still not caught at review
time from either side. This remains the largest fidelity gap that needs no API key.

### 3.4 Fix the budget knobs before measuring anything with them

The previous plan said the budget settings are "plumbed but never exercised". One is not
plumbed at all and another does not mean what its own config comment says.

| Setting | State |
|---|---|
| `llm_timeout_seconds` | Declared at `config.py:83`, documented at `settings.example.yaml:14`, used nowhere in `src/`. Dead configuration. |
| `max_budget_usd` | Passed to `ClaudeAgentOptions` per call, not per run. `settings.example.yaml:12` calls it "cap a review's spend"; a $2.50 setting permits roughly $360 across a worst-case review. |
| `max_turns` | Enforced, also per call. |
| `max_regeneration_attempts` | Genuinely enforced, and bounds two separate loops. |

The call count is larger than the previous estimate. A happy-path review is 18 logical
calls: one classify, thirteen rows, then claims, judgement, source-quality and synthesis.
Worst case is 48 once the re-probes and the repair loop are counted, and `_call` itself
retries up to `max_regeneration_attempts + 1` times on schema failure with every attempt
hitting the wire — so roughly **144 provider calls**, about eight times the previous figure
of "roughly twenty".

**Do:** accumulate `cost_usd` across `sweep.calls` and abort inside the `_call` attempt loop
when the run budget is exhausted; wrap `generate` in an `asyncio.timeout`; give the abort
its own error class and exit code, since a partial sweep must not be published under the
never-soften rule. Then measure. The measurement stays blocked on 0.2.

**Done — DEC-F17.** Cost accumulates across `sweep.calls` and is checked inside `_call`'s
attempt loop, before each provider call, so one logical call cannot burn several times what
is left; a backend reporting no cost contributes zero rather than aborting the run. Each call
runs under `asyncio.timeout(llm_timeout_seconds)`, making that setting live. Both raise
`RunAbortedError` subclasses carrying the new `ExitCode.RUN_ABORTED = 6`, and a test asserts
an aborted run writes no state and publishes no report. Enforcement lives in `pipeline.py`
rather than the SDK adapter, because that adapter is the one approved coverage omit (DEC-F8)
and a budget check written there would ship unmeasured.

**Still blocked on 0.2:** the measurement itself. What a real review costs and how long it
takes is unknown until something runs against the live SDK, so the settings now enforce a
number nobody has calibrated.

---

## Tranche 4 — Durability work, once the signal is trustworthy.

### 4.1 Schema migration seams

Both real version gates — `harness/oracle.py:69` and `harness/state.py:72` — read
`schema_version` and reject anything unknown. A `_MIGRATIONS` chain goes between that read
and `model_validate` in exactly those two places.

One correction: the previous plan asked for frozen v1 fixtures for "the oracle YAML and the
report contract". The report contract has no read side. `REPORT_CONTRACT_VERSION` is
rendered into the markdown and serialised into `--output-json`, and nothing anywhere parses
a report back; the only assertion is that the key exists. A migration chain for a format
nothing reads is meaningless. What the report contract needs is a documented consumer
promise for `--output-json`, which is a docs task. The oracle half is right and is the bulk
of the work: `tests/fixtures/` holds exactly one frozen fixture, `state/v1-example.md`, and
authoring a full frozen v1 oracle is the expensive part, since `tests/factories.py` builds
against the live model and `CLAUDE.md` forbids using the shipped product data for engine
behaviour.

Coupling worth budgeting for: `SchemaModel` sets `extra="forbid"`, so a migration must
operate on raw dicts and emit exactly the current field set, which means each migration
needs a frozen copy of the old field list to mean anything.

**Size:** roughly 10-14h, 2 pull requests, plus a decision-log entry.

**Done — DEC-F18.** `harness/migrations.py` holds one reusable `MigrationChain`, applied
between the version read and `model_validate` in both `harness/oracle.py` and
`harness/state.py`. The chain is empty (v1 is current for both), so it is an identity pass
whose plumbing is tested, and `supported_versions` truncates at a gap rather than skipping a
version, so a half-registered chain cannot claim to read a file it would mangle. The frozen
v1 oracle fixture is at `tests/fixtures/oracle/v1-example.yaml` — frozen bytes with a header
saying not to regenerate it, because `tests/factories.py::make_oracle` builds against the
live model and can never prove a v1 file still loads. Both durable read formats now have one.
The report contract is excluded and stays excluded: it has no read side, so what it needed
was a documented consumer promise, which `README.md` now carries.

### 4.2 State POSIX-only, or support Windows properly

There are two independent Windows breaks, not one:

- `harness/state.py:11` imports `fcntl` at module scope.
- `harness/assets.py:153-155` fails a hook lacking `S_IXUSR`. Windows never sets the execute
  bit, so `assets validate` exits 1 on a clean Windows checkout. The previous plan did not
  mention this.

The previous plan also said the CLI is unimportable on Windows. It is not: `cli.py` imports
`state` lazily, so `version`, `oracles`, `agents`, `assets validate` and `decisions check`
all run. Only `review` and `state show` fail, and they fail as a bare exit-5 "unexpected
error" rather than a clear message. Four sites write text without `newline="\n"` and would
emit CRLF: `pipeline.py:641`, `pipeline.py:644`, `cli.py:162-169` and `cli.py:295`.
`state.py:90` gets it right.

**Recommendation:** state POSIX-only. Nothing today says so — README, architecture and
`pyproject.toml` carry no statement and no `Operating System` classifier — so the project
silently claims portability it does not have. A README line, a classifier and a clean
`ConfigError` instead of the exit-5 crash is about 2h and one pull request. Full support is
10-22h and 2-3 pull requests, most of it the unbounded cost of a `windows-latest` leg the
suite has never seen.

**Done, the recommended half — and more than the recommendation.** `pyproject.toml` carries
`Operating System :: POSIX`, `POSIX :: Linux` and `MacOS` classifiers, and `README.md` and
`docs/architecture.md` say POSIX is what is tested and that there is no Windows CI leg. Both
breaks are actually fixed rather than merely declared: `fcntl` moved behind
`harness/filelock.py`, which binds both platform backends at module scope so the non-POSIX
branch is substitutable in a test rather than pragma'd past the coverage gate, and
`assets validate`'s execute-bit check is POSIX-gated. The four CRLF-emitting `write_text`
calls pin `newline="\n"`, with a test scanning `src/` so a fifth cannot appear, and a second
test forbids a bare `import fcntl` outside the locking module.

**Still open: full Windows support.** No `windows-latest` CI leg exists, so nothing above is
verified on Windows — the classifier states what is tested, and that is the point of it. The
`msvcrt` locking branch is exercised only by substitution, and the "clean `ConfigError`
instead of an exit-5 crash" fallback is not written; `FileLockUnavailableError` is a bare
`RuntimeError` and would still surface as exit 5 on a platform with neither backend. The
10-22h estimate stands, and most of it is still the unbounded cost of a leg the suite has
never seen.

---

## Tranche 5 — Longer term. Not before the reviewer has been used in anger.

Neither item has moved, and neither should have: the precondition is use, not effort. The
reviewer has still never run against the live SDK (0.2), so nothing yet says which parts of
the harness generalise.

### 5.1 The `adversarial-reviewer` companion

The specification names a sibling agent that reviews **diffs** where sutton-review reviews
**designs**. The plugin seam exists and the registry takes it in one line, but the review
model is genuinely different: a diff has no artifact class, no doctrine sweep in the same
sense, and its state is a pull request rather than a document. Worth building only once the
design reviewer has been used enough to know which parts of the harness generalise.

### 5.2 Multi-oracle review

A blueprint could plausibly be reviewed against two corpora at once, say an RL doctrine and
a safety-engineering doctrine. Nothing in the harness assumes a single oracle except the
CLI's `--oracle` flag and the report contract's single `oracle_id`. How severities from
different corpora combine is a product decision before it is an engineering one.

---

## Documentation and release debt

Small, and all of it is the honesty class this repository polices in others.

- ~~`CHANGELOG.md:14` still says the Unreleased work is "not yet in `main`". PR #7 merged on
  2026-08-22. This is the second time this exact line has gone stale.~~ **Done**, and the
  line now scopes what the section covers rather than asserting where it lives.
- ~~`CHANGELOG.md` says the `inspect-state` skill's guard "subprocess-confirms every command
  it names". It does not. `tests/unit/test_assets.py:129` uses `CliRunner` in-process, and
  the test's own docstring says so. The test is sound; the changelog overclaims it.~~
  **Done.** The entry now says what the test does, and says that it previously overclaimed.
- `pyproject.toml:11` declares `license = { text = "MIT" }` and there was no `LICENSE` file
  in the repository, so the package claimed a licence it did not ship. **Done** — the MIT
  text is now at `LICENSE`, matching the declared metadata.
- DEC-F9, DEC-F11 and `docs/architecture.md` §8 each assert a control that 1.1, 1.4 and 1.5
  show does not hold as written. Correct the text in the same pull requests that fix the
  code, so the decision log never describes a mechanism that is not there. **Half done.**
  `docs/architecture.md` §8 is rewritten: the tool-honesty bullet now describes authority
  binding and says outright that the previous text asserted the opposite, the fetch bullet
  covers the non-canonical IPv4 forms, RFC 6598 and the scheme check, and read scoping has
  its own deny-by-default bullet. The decision log took the other route: DEC-F9's and
  DEC-F11's original text is left intact and DEC-F12, DEC-F15 and DEC-F16 supersede or extend
  them, each stating what the earlier entry got wrong. That is defensible for an append-only
  log, but a reader who stops at DEC-F9 still reads a claim that no longer holds, so the two
  older entries need a forward pointer.
- The version is still `0.1.0` with no tag and no dated release section. Once Tranches 1 and
  2 land, cut `0.2.0`. **Now actionable:** Tranche 1 is complete apart from 1.7's read half,
  and Tranche 2 apart from 2.3 and 2.4.
- Deterministic findings render under `placeholder_row_id: "-"`, producing keys like
  `-+duplicate-definition-alpha` in `state show`. Cosmetic, but it is the operator-facing
  identifier for the recurrence mechanism. **Still open**, unchanged.

---

## Explicitly not planned

- **Approving or merging on the reviewer's behalf.** The escalation rule hands recurring
  disagreements to the owner by design; automating the disposition would defeat it.
- **Softening any output.** The verification-log failure mode (exit 3) and the "never
  soften" rule are load-bearing; a "summary mode" that drops the verification log would make
  the report unfalsifiable.
- **A hosted service.** The tool reads untrusted documents and holds an API key; running it
  as a shared service is a materially different threat model than the current one.
- **A state store that arbitrates multi-writer merges.** 1.3 fixes the silent-corruption
  half by detecting and failing. A store that can genuinely merge concurrent cycle histories
  is 20h+ and a different backing store, and a single-user reviewer does not need one.
- **A redirect-validating fetcher.** DEC-F11 documents this residual risk and declines it as
  over-engineering for a single-user offline reviewer. That judgement still holds, and 1.1
  and 1.5 are the cheaper and more urgent parts of the same surface.
