---
name: guard-check
description: >
  Prove a test actually fails when the code it guards is reverted, using
  scripts/verify_guard.py. Use after writing or changing any test that claims to enforce a
  security control, a severity rule, or a contract; when reviewing a test you did not
  write; when a bug shipped despite a green suite; and whenever someone says a test
  "covers" something. Also use when asked to check whether tests are real, whether
  coverage is meaningful, or why a defect was not caught.
---

# Does this test guard anything?

A test that still passes when the behaviour it names is removed is not a test. This
repository has shipped that mistake at every level of the stack, and every instance was
green in CI, counted in 95% branch coverage, and in one case backed by a 100% mutation
kill rate:

| What the test claimed | Why it passed anyway |
|---|---|
| Model prose cannot forge a findings row | It grepped for an escape sequence, not for a row count |
| Glob patterns cannot escape the read roots | Every case put its traversal before the first metacharacter — the shapes the code already caught |
| The budget check runs inside the retry loop | It watched a value the *neighbouring* statement computes |
| The adapter never uses `bypassPermissions` | It re-read a fixture that had just set `dontAsk` |
| Three oracle rows are verified | Written against data that had just been hand-edited |
| A concurrent write cannot erase cycle history | The store was tested directly; the pipeline's opt-in was not |

The last one is the general shape and the most common: **the leaf behaviour is tested and
the wiring that switches it on is not.** Five separate one-line deletions in
`pipeline.py` and `cli.py` each left the whole suite passing.

## The check

```bash
uv run python scripts/verify_guard.py \
    --file src/creative_agent/harness/security.py \
    --find '    if _refused_glob_shape(pattern):
        return False
' \
    --replace '' \
    --test 'tests/unit/test_claude_sdk_adapter.py::TestGlobEscapesThatSurvivedTheFirstFix'
```

It runs the tests on the current tree (they must pass), applies the revert, runs them
again (they must now fail), and restores the file byte for byte. Exit `0` the guard
holds, `1` the guard is weak, `2` the check could not run — a red baseline or a `--find`
string that does not match uniquely, neither of which says anything about the test.

For a multi-file revert, or one with awkward quoting, use a spec:

```bash
uv run python scripts/verify_guard.py --spec /tmp/guard.json
```
```json
{"name": "DEC-F20 protocol tools", "tests": ["tests/unit/test_claude_sdk_adapter.py::TestTheSdkResponseChannelIsNotACapability"],
 "edits": [{"file": "src/creative_agent/harness/llm/claude_sdk.py", "find": "...", "replace": ""}]}
```

Specs are ad hoc — write them to `/tmp`, not into the repo. A checked-in spec is a copy of
the code that rots the moment the code moves, and a stale revert that no longer applies
reports exit 2 forever.

## When the guard is weak

Do not add assertions to the existing test. Work out what it is actually asserting — it is
almost always one of these — and rewrite it:

- **It asserts an intermediate value.** Something computed near the code under test, but
  not by it. Assert the *outcome*: the exception raised, the exit code, the row count in
  the rendered report, the number of calls that reached the backend.
- **It asserts its own fixture.** The expected value is built by the same helper as the
  actual, or set by the test three lines above. Compute the expectation independently, or
  assert a property that the fixture cannot satisfy on its own.
- **It enumerates the cases that already pass.** Parametrized lists grow towards what the
  implementation handles. Add the case one character away from a case that fails: a
  metacharacter moved left, a field omitted, an argument made relative.
- **It tests a leaf, not the wiring.** Add an integration test that runs the composed
  pipeline or invokes the CLI, and assert the observable outcome there.

Then run the check again. A rewritten test that still reports weak is telling you the
behaviour is genuinely not reachable from that level — move the test, do not weaken the
revert.

## Where this belongs in the workflow

Run it on the specific tests you added, before `make gate` and before pushing. It is not a
gate over the whole suite — the revert has to be chosen by someone who knows what the code
is supposed to do, which is exactly why it cannot be automated into CI and has to be a
habit instead.

Related: `.claude/skills/review-gate` runs the full local gate; this checks whether the
part of the gate you just added means anything.
