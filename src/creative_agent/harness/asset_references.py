"""Do the assets' instructions still point at things that exist? (DEC-F29)

`assets.py` validates an asset's *shape* — front matter keys, a name that matches its
filename, a description long enough to trigger, a hook that is executable. It says nothing
about the asset's body, which is the part an agent actually follows.

That gap is the same one this repository keeps finding in its tests: a check that passes
for a reason unrelated to what it claims. A skill can name `scripts/verify_guard.py` after
the script is renamed, tell an agent to run `make guard` when no such target exists, cite
`DEC-F26` when the decision log stops at F25, or invoke `creative-agent verify` which was
never a subcommand — and every one of those passes `assets validate` today. The failure is
silent and lands mid-session, in someone else's context, as an agent confidently running a
command that does not work.

Four checks, all deterministic and all in-process:

1. **Repository paths** mentioned in the body exist on disk.
2. **`make <target>`** names a target the Makefile actually declares.
3. **`creative-agent <subcommand>`** names a registered subcommand.
4. **`DEC-F<n>`** citations name an entry the decision log actually contains.

Deliberately no shell parsing. `bash -n` on every fenced block would catch a fifth class,
and it would make a validation library shell out — untestable without a subprocess, slower,
and dependent on which shell the machine has. These four cover the references that go stale
when code moves, which is the failure that actually happens.

What is checked is data (`ReferenceRules`), not literals at a call site: a repository with a
different layout supplies different prefixes rather than editing this module.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Inline code spans and fenced-block lines are where an asset names a real thing. Prose
# outside them is deliberately not scanned: an asset may legitimately discuss `docs/design.md`
# as an example artifact, and flagging that would train people to ignore this check.
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_FENCED_BLOCK = re.compile(r"^```[a-zA-Z]*\n(.*?)^```", re.DOTALL | re.MULTILINE)

_MAKE_TARGET = re.compile(r"\bmake\s+([a-z][a-z0-9-]*)\b")
_CLI_SUBCOMMAND = re.compile(r"\bcreative-agent\s+([a-z][a-z0-9-]*)\b")
_DECISION_ID = re.compile(r"\b(DEC-F\d+)\b")
_MAKEFILE_TARGET_LINE = re.compile(r"^([a-zA-Z][a-zA-Z0-9_-]*):", re.MULTILINE)

# A token is only treated as a repository path when it is unambiguous: no globs, no shell
# expansion, no angle-bracket placeholder, and no whitespace. `scripts/*.py` and
# `<file_path>` are documentation, not references.
_NOT_A_LITERAL_PATH = re.compile(r"[*?<>$\s{}|]")


@dataclass(frozen=True)
class ReferenceRules:
    """Which references to check, and where to resolve them.

    `path_prefixes` is what makes a token a repository path rather than an example: only
    tokens starting with one of these are resolved against the tree. A repository with a
    different layout passes different prefixes; nothing here is specific to this one except
    the defaults below.
    """

    path_prefixes: tuple[str, ...]
    makefile: str
    decision_log: str
    # Paths under these prefixes are produced by running the harness, not committed, so an
    # asset naming one is never a stale reference. The first run of this checker flagged
    # three of them in `live-verify` — and they were the paths that skill tells the reader
    # to DELETE after a live run, which by definition should not exist. A checker whose
    # false positives are the instructions it is validating trains people to ignore it.
    runtime_prefixes: tuple[str, ...] = ()


DEFAULT_REFERENCE_RULES = ReferenceRules(
    path_prefixes=("src/", "tests/", "scripts/", "docs/", ".claude/", ".github/", "config/"),
    makefile="Makefile",
    decision_log="docs/decision-log.md",
    runtime_prefixes=("docs/review-log/",),
)


@dataclass(frozen=True)
class ReferenceContext:
    """What a check resolves references against.

    One object rather than four parameters threaded through every layer: the checks below
    all need the same context, and a signature that grows a parameter per check is how a
    caller ends up passing them in the wrong order.
    """

    repo_root: Path
    known_subcommands: frozenset[str] = frozenset()
    rules: ReferenceRules = DEFAULT_REFERENCE_RULES


def _referenced_tokens(body: str) -> list[str]:
    """Every code span and every whitespace-separated word inside a fenced block."""
    tokens = _CODE_SPAN.findall(body)
    for block in _FENCED_BLOCK.findall(body):
        tokens.extend(block.split())
    return tokens


def _declared_make_targets(makefile: Path) -> set[str]:
    if not makefile.is_file():
        return set()
    return set(_MAKEFILE_TARGET_LINE.findall(makefile.read_text(encoding="utf-8")))


def _logged_decisions(decision_log: Path) -> set[str]:
    if not decision_log.is_file():
        return set()
    return set(_DECISION_ID.findall(decision_log.read_text(encoding="utf-8")))


def check_paths(body: str, context: ReferenceContext) -> list[str]:
    """Repository paths named in code spans or fenced blocks that are not on disk."""
    defects: list[str] = []
    for token in _referenced_tokens(body):
        candidate = token.strip().rstrip(".,;:")
        if not candidate.startswith(context.rules.path_prefixes):
            continue
        if context.rules.runtime_prefixes and candidate.startswith(context.rules.runtime_prefixes):
            continue
        if _NOT_A_LITERAL_PATH.search(candidate):
            continue
        if not (context.repo_root / candidate).exists():
            defects.append(
                f"references {candidate!r}, which does not exist; an asset naming a moved "
                "or deleted path sends an agent to a file that is not there"
            )
    return defects


def _code_text(body: str) -> str:
    """Only the code spans and fenced blocks, joined.

    Every check reads this rather than the raw body. `check_paths` already did, with the
    reason in the module docstring — an asset may legitimately discuss an example path in
    prose. The `make` and subcommand checks did not, so "Please **make sure** the gate is
    green" reported a missing target `sure`, and "the **creative-agent harness**" reported
    an unregistered subcommand `harness`. The shipped assets passed only because every such
    phrase happened to sit in front matter, which is stripped before the body is scanned.
    A checker whose first false positive is an ordinary English sentence gets ignored.
    """
    return "\n".join(_referenced_tokens(body))


def check_make_targets(body: str, context: ReferenceContext) -> list[str]:
    """`make <target>` instructions naming a target the Makefile does not declare."""
    targets = _declared_make_targets(context.repo_root / context.rules.makefile)
    if not targets:
        # No Makefile is a fact about the repository, not about the asset. Reporting every
        # `make` reference as broken would bury the real findings under a wall of
        # consequences from one missing file.
        return []
    return [
        f"tells the reader to run `make {target}`, which the Makefile does not declare "
        f"(targets: {', '.join(sorted(targets))})"
        for target in _MAKE_TARGET.findall(_code_text(body))
        if target not in targets
    ]


def check_subcommands(body: str, context: ReferenceContext) -> list[str]:
    """`creative-agent <sub>` invocations that are not registered subcommands."""
    if not context.known_subcommands:
        return []
    return [
        f"invokes `creative-agent {sub}`, which is not a registered subcommand "
        f"({', '.join(sorted(context.known_subcommands))})"
        for sub in _CLI_SUBCOMMAND.findall(_code_text(body))
        if sub not in context.known_subcommands
    ]


def check_decisions(body: str, context: ReferenceContext) -> list[str]:
    """`DEC-F<n>` citations with no entry in the decision log."""
    logged = _logged_decisions(context.repo_root / context.rules.decision_log)
    if not logged:
        return []
    return [
        f"cites {decision}, which has no entry in {context.rules.decision_log}; a citation "
        "to a decision nobody wrote is worse than none"
        for decision in _DECISION_ID.findall(body)
        if decision not in logged
    ]


# The checks, as data. Adding one is appending to this tuple, which is also what makes each
# individually testable without driving the whole validator.
REFERENCE_CHECKS: tuple[Callable[[str, ReferenceContext], list[str]], ...] = (
    check_paths,
    check_make_targets,
    check_subcommands,
    check_decisions,
)


def missing_references(
    body: str,
    context: ReferenceContext,
    checks: tuple[Callable[[str, ReferenceContext], list[str]], ...] = REFERENCE_CHECKS,
) -> list[str]:
    """Every reference in `body` that does not resolve, as messages ready for a defect.

    An empty list is the only pass. Each message names the reference and what it should
    point at, because the person reading it is fixing an asset they may not have written.
    """
    defects = [message for check in checks for message in check(body, context)]
    # Deduplicate while keeping order: an asset that names one dead path in four places has
    # one defect to fix, and four copies of the same line makes the others harder to see.
    seen: set[str] = set()
    unique: list[str] = []
    for defect in defects:
        if defect not in seen:
            seen.add(defect)
            unique.append(defect)
    return unique
